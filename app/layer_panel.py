"""Layer panel for managing loaded layers and groups."""
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QMenu, QInputDialog, QMessageBox, QStyle, QApplication,
    QLabel, QSplitter
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor


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
        # Faster scrolling with many items
        self.tree.setUniformRowHeights(True)

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

    def add_layer(self, layer_id: str, file_path: str,
                  parent: QTreeWidgetItem = None, visible: bool = True):
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

    def add_group(self, name: str, parent: QTreeWidgetItem = None,
                  visible: bool = True):
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

            # If turning ON, ensure all parent groups are also checked
            if checked:
                self._check_parent_groups(item)

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

            # If turning ON, ensure all parent groups are also checked
            if checked:
                self._check_parent_groups(item)

    def _check_parent_groups(self, item: QTreeWidgetItem):
        """Ensure all parent groups of an item are checked.

        Args:
            item: The item whose parents should be checked
        """
        self.tree.blockSignals(True)
        parent = item.parent()
        while parent is not None:
            if parent.checkState(0) != Qt.Checked:
                parent.setCheckState(0, Qt.Checked)
            parent = parent.parent()
        self.tree.blockSignals(False)

    def _check_parents_of_visible_items(self):
        """Ensure all parent groups are checked for any checked (visible) items.

        Called after drag-drop to fix parent states when items are moved.
        """
        self.tree.blockSignals(True)

        def check_item(item: QTreeWidgetItem):
            item_type = item.data(0, Qt.UserRole + 1)
            is_checked = item.checkState(0) == Qt.Checked

            if item_type == "layer":
                # If this layer is checked, ensure all parents are checked
                if is_checked:
                    parent = item.parent()
                    while parent is not None:
                        if parent.checkState(0) != Qt.Checked:
                            parent.setCheckState(0, Qt.Checked)
                        parent = parent.parent()
            elif item_type == "group":
                # If this group is checked, ensure all parents are checked
                if is_checked:
                    parent = item.parent()
                    while parent is not None:
                        if parent.checkState(0) != Qt.Checked:
                            parent.setCheckState(0, Qt.Checked)
                        parent = parent.parent()
                # Recurse into children
                for i in range(item.childCount()):
                    check_item(item.child(i))

        # Check all top-level items
        for i in range(self.tree.topLevelItemCount()):
            check_item(self.tree.topLevelItem(i))

        self.tree.blockSignals(False)

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

    def _toggle_group_children(
            self, item: QTreeWidgetItem, checked: bool, emit_progress: bool):
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

        # Ensure parent groups are checked for any checked items that were
        # moved
        self._check_parents_of_visible_items()

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
        # Return in top-to-bottom tree order.
        # MapCanvas expects the list in top-to-bottom order so that
        # assigning increasing z-values makes bottom tree items render on top.
        return layers

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
            layer_ids = [
                it.data(
                    0,
                    Qt.UserRole) for it in selected if it.data(
                    0,
                    Qt.UserRole +
                    1) == "layer"]
            if layer_ids:
                menu.addSeparator()
                turn_on_action = menu.addAction("Turn on layers")
                turn_on_action.triggered.connect(
                    lambda _, ids=layer_ids: self.check_layers(ids))

                turn_off_action = menu.addAction("Turn off layers")
                turn_off_action.triggered.connect(
                    lambda _, ids=layer_ids: self.uncheck_layers(ids))

                menu.addSeparator()
                remove_action = menu.addAction("Remove")
                remove_action.triggered.connect(
                    lambda _, items=selected: [
                        self._remove_item(it) for it in items])

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
                zoom_action.triggered.connect(
                    lambda: self.zoom_to_layer_requested.emit(layer_id))

            # Group-specific options
            if item_type == "group":
                menu.addSeparator()
                select_all_action = menu.addAction("Select all children")
                select_all_action.triggered.connect(
                    lambda: self._set_all_children_checked(item, True))

                unselect_all_action = menu.addAction("Unselect all children")
                unselect_all_action.triggered.connect(
                    lambda: self._set_all_children_checked(item, False))

                menu.addSeparator()
                expand_all_action = menu.addAction("Expand All")
                expand_all_action.triggered.connect(
                    lambda: self._expand_all_children(item))

                collapse_all_action = menu.addAction("Collapse All")
                collapse_all_action.triggered.connect(
                    lambda: self._collapse_all_children(item))

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

    def _expand_all_children(self, item: QTreeWidgetItem):
        """Recursively expand an item and all its children.

        Args:
            item: The item to expand along with all descendants
        """
        item.setExpanded(True)
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, Qt.UserRole + 1) == "group":
                self._expand_all_children(child)

    def _collapse_all_children(self, item: QTreeWidgetItem):
        """Recursively collapse an item and all its children.

        Args:
            item: The item to collapse along with all descendants
        """
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, Qt.UserRole + 1) == "group":
                self._collapse_all_children(child)
        item.setExpanded(False)

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
        if not layer_ids:
            return

        layer_ids_set = set(layer_ids)
        total = len(layer_ids_set)
        self.batch_visibility_started.emit(total)
        progress_count = [0]
        changed_layers = []  # Track which layers were actually changed

        # Block signals to prevent cascading _on_item_changed calls
        self.tree.blockSignals(True)

        def find_and_uncheck(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_uncheck(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    layer_id = parent.data(0, Qt.UserRole)
                    if layer_id in layer_ids_set and parent.checkState(
                            0) == Qt.Checked:
                        parent.setCheckState(0, Qt.Unchecked)
                        changed_layers.append(layer_id)
                        progress_count[0] += 1
                        self.batch_visibility_progress.emit(progress_count[0])
                        if progress_count[0] % 10 == 0:
                            QApplication.processEvents()
                else:
                    for i in range(parent.childCount()):
                        find_and_uncheck(parent.child(i))

        find_and_uncheck()

        self.tree.blockSignals(False)

        # Emit visibility changed signals for each layer that was actually
        # changed
        for layer_id in changed_layers:
            self.layer_visibility_changed.emit(layer_id, False)

        self.batch_visibility_finished.emit()

    def check_layers(self, layer_ids: list[str]):
        """Check (show) layers by their IDs.

        Args:
            layer_ids: List of layer IDs to check
        """
        if not layer_ids:
            return

        layer_ids_set = set(layer_ids)
        total = len(layer_ids_set)
        self.batch_visibility_started.emit(total)
        progress_count = [0]
        changed_layers = []  # Track which layers were actually changed

        # Block signals to prevent cascading _on_item_changed calls
        self.tree.blockSignals(True)

        def find_and_check(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_check(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    layer_id = parent.data(0, Qt.UserRole)
                    if layer_id in layer_ids_set and parent.checkState(
                            0) == Qt.Unchecked:
                        parent.setCheckState(0, Qt.Checked)
                        changed_layers.append(layer_id)
                        progress_count[0] += 1
                        self.batch_visibility_progress.emit(progress_count[0])
                        if progress_count[0] % 10 == 0:
                            QApplication.processEvents()
                else:
                    for i in range(parent.childCount()):
                        find_and_check(parent.child(i))

        find_and_check()

        self.tree.blockSignals(False)

        # Emit visibility changed signals for each layer that was actually
        # changed
        for layer_id in changed_layers:
            self.layer_visibility_changed.emit(layer_id, True)

        self.batch_visibility_finished.emit()

    def get_checked_layers_in_selected_group(self) -> list[str]:
        """Get list of checked layer IDs within the currently selected group.

        If a group is selected, returns checked layers within that group.
        If a layer is selected, returns checked layers in its parent group.
        If nothing is selected or selection is at root level, returns empty list.

        Returns:
            List of layer IDs that are checked, in tree order (top to bottom).
        """
        selected = self.tree.selectedItems()
        if not selected:
            return []

        item = selected[0]  # Use first selected item
        item_type = item.data(0, Qt.UserRole + 1)

        # Find the group to search in
        if item_type == "group":
            group_item = item
        elif item_type == "layer":
            group_item = item.parent()
            if group_item is None:
                return []  # Layer at root level, no group
        else:
            return []

        # Collect checked layers from this group (recursively)
        checked_layers = []
        self._collect_checked_layers(group_item, checked_layers)
        return checked_layers

    def _collect_checked_layers(
            self, item: QTreeWidgetItem, checked_layers: list):
        """Recursively collect checked layer IDs from an item and its children."""
        for i in range(item.childCount()):
            child = item.child(i)
            child_type = child.data(0, Qt.UserRole + 1)
            if child_type == "layer":
                if child.checkState(0) == Qt.Checked:
                    checked_layers.append(child.data(0, Qt.UserRole))
            elif child_type == "group":
                self._collect_checked_layers(child, checked_layers)

    def get_all_layers_in_selected_group(self) -> list[str]:
        """Get list of ALL layer IDs within the currently selected group.

        Similar to get_checked_layers_in_selected_group but returns all layers
        regardless of check state.

        Returns:
            List of layer IDs in tree order (top to bottom).
        """
        selected = self.tree.selectedItems()
        if not selected:
            return []

        item = selected[0]
        item_type = item.data(0, Qt.UserRole + 1)

        if item_type == "group":
            group_item = item
        elif item_type == "layer":
            group_item = item.parent()
            if group_item is None:
                return []
        else:
            return []

        all_layers = []
        self._collect_all_layers(group_item, all_layers)
        return all_layers

    def _collect_all_layers(self, item: QTreeWidgetItem, layers: list):
        """Recursively collect ALL layer IDs from an item and its children."""
        for i in range(item.childCount()):
            child = item.child(i)
            child_type = child.data(0, Qt.UserRole + 1)
            if child_type == "layer":
                layers.append(child.data(0, Qt.UserRole))
            elif child_type == "group":
                self._collect_all_layers(child, layers)

    def get_selected_group_name(self) -> str:
        """Get the name of the currently selected group.

        Returns:
            Group name if a group is selected, or the parent group name if a layer is selected.
            Returns empty string if nothing is selected or selection is at root level.
        """
        selected = self.tree.selectedItems()
        if not selected:
            return ""

        item = selected[0]
        item_type = item.data(0, Qt.UserRole + 1)

        if item_type == "group":
            return item.text(0)
        elif item_type == "layer":
            parent = item.parent()
            if parent is not None:
                return parent.text(0)

        return ""

    def clear(self):
        """Clear all items from the tree."""
        self.tree.clear()

    def set_layer_checked(self, layer_id: str, checked: bool):
        """Set the check state of a specific layer without emitting signals.

        Args:
            layer_id: The layer ID to update
            checked: True to check, False to uncheck
        """
        self.tree.blockSignals(True)

        def find_and_set(parent=None) -> QTreeWidgetItem | None:
            """Find and set the layer, returning the item if found."""
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    result = find_and_set(self.tree.topLevelItem(i))
                    if result:
                        return result
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) == layer_id:
                        parent.setCheckState(
                            0, Qt.Checked if checked else Qt.Unchecked)
                        return parent
                else:
                    for i in range(parent.childCount()):
                        result = find_and_set(parent.child(i))
                        if result:
                            return result
            return None

        found_item = find_and_set()

        # If turning ON, also check all parent groups
        if checked and found_item:
            parent = found_item.parent()
            while parent is not None:
                if parent.checkState(0) != Qt.Checked:
                    parent.setCheckState(0, Qt.Checked)
                parent = parent.parent()

        self.tree.blockSignals(False)

    def is_layer_checked(self, layer_id: str) -> bool:
        """Check if a specific layer is checked (visible).

        Args:
            layer_id: The layer ID to check

        Returns:
            True if the layer is checked, False otherwise
        """
        def find_and_check(parent=None) -> bool | None:
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    result = find_and_check(self.tree.topLevelItem(i))
                    if result is not None:
                        return result
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "layer":
                    if parent.data(0, Qt.UserRole) == layer_id:
                        return parent.checkState(0) == Qt.Checked
                else:
                    for i in range(parent.childCount()):
                        result = find_and_check(parent.child(i))
                        if result is not None:
                            return result
            return None

        result = find_and_check()
        return result if result is not None else False

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
    # lon, lat - zoom to specific coordinates
    zoom_to_label_requested = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        # file_path -> layer_id (from main panel)
        self._layer_id_map: dict[str, str] = {}
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

    def refresh(self, project, visibility_checker=None):
        """Refresh the tree with current labels from the project.

        Args:
            project: The LabelProject containing images and labels
            visibility_checker: Optional callable(file_path) -> bool to check layer visibility
        """
        self.tree.blockSignals(True)
        self.tree.clear()

        # Group labels by object_id
        # object_id -> list of (label_id, image_name, image_path, lon, lat,
        # class_name)
        object_groups: dict[str,
                            list[tuple[int, str, str, float, float, str]]] = {}

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
            # Check state will be set after children are added based on their
            # visibility
            group_item.setIcon(0, style.standardIcon(QStyle.SP_DirIcon))

            # Bold font for groups, different colors based on link status
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            if label_count > 1:
                # Steel blue for linked
                group_item.setForeground(0, QColor(70, 130, 180))
            else:
                # Cornflower blue for single
                group_item.setForeground(0, QColor(100, 149, 237))

            self.tree.addTopLevelItem(group_item)

            # Add each label as a child
            any_visible = False
            for label_id, image_name, file_path, lon, lat, class_name in labels:
                label_item = QTreeWidgetItem()
                label_item.setText(
                    0, f"#{label_id}: {image_name} [{class_name}]")
                label_item.setData(0, Qt.UserRole, file_path)
                label_item.setData(0, Qt.UserRole + 1, "label")
                label_item.setData(0, Qt.UserRole + 2, label_id)
                label_item.setData(0, Qt.UserRole + 3, lon)
                label_item.setData(0, Qt.UserRole + 4, lat)
                label_item.setFlags(
                    label_item.flags() | Qt.ItemIsUserCheckable)

                # Check visibility - default to unchecked if no checker
                # provided
                is_visible = visibility_checker(
                    file_path) if visibility_checker else False
                label_item.setCheckState(
                    0, Qt.Checked if is_visible else Qt.Unchecked)
                if is_visible:
                    any_visible = True

                label_item.setToolTip(
                    0, f"Label #{label_id} on {file_path}\nLon: {
                        lon:.6f}, Lat: {
                        lat:.6f}")
                label_item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
                group_item.addChild(label_item)

            # Set group check state based on children
            group_item.setCheckState(
                0, Qt.Checked if any_visible else Qt.Unchecked)

            group_item.setExpanded(True)

        self.tree.blockSignals(False)

    def add_label(self, label, image, visibility_checker=None):
        """Add a single label to the tree incrementally (O(1) instead of full refresh).

        Args:
            label: The PointLabel to add
            image: The ImageData the label belongs to
            visibility_checker: Optional callable(file_path) -> bool to check layer visibility
        """
        self.tree.blockSignals(True)
        style = QApplication.style()

        object_id = label.object_id

        # Find existing group for this object_id
        group_item = None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.UserRole) == object_id:
                group_item = item
                break

        # Create new group if needed
        if group_item is None:
            group_item = QTreeWidgetItem()
            short_id = object_id[:8] + "..."  # Truncate UUID for display
            group_item.setText(0, f"Object: {short_id} (1)")
            group_item.setData(0, Qt.UserRole, object_id)
            group_item.setData(0, Qt.UserRole + 1, "group")
            group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
            group_item.setIcon(0, style.standardIcon(QStyle.SP_DirIcon))

            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            # Cornflower blue for single label
            group_item.setForeground(0, QColor(100, 149, 237))
            group_item.setCheckState(0, Qt.Unchecked)
            group_item.setExpanded(True)

            self.tree.addTopLevelItem(group_item)
        else:
            # Update group label count and color
            new_count = group_item.childCount() + 1
            short_id = object_id[:8] + "..."
            group_item.setText(0, f"Object: {short_id} ({new_count})")
            if new_count > 1:
                # Steel blue for linked
                group_item.setForeground(0, QColor(70, 130, 180))

        # Create label item
        label_item = QTreeWidgetItem()
        label_item.setText(
            0, f"#{label.id}: {image.name} [{label.class_name}]")
        label_item.setData(0, Qt.UserRole, image.path)
        label_item.setData(0, Qt.UserRole + 1, "label")
        label_item.setData(0, Qt.UserRole + 2, label.id)
        label_item.setData(0, Qt.UserRole + 3, label.lon)
        label_item.setData(0, Qt.UserRole + 4, label.lat)
        label_item.setFlags(label_item.flags() | Qt.ItemIsUserCheckable)

        # Check visibility
        is_visible = visibility_checker(image.path) if visibility_checker else False
        label_item.setCheckState(0, Qt.Checked if is_visible else Qt.Unchecked)

        label_item.setToolTip(
            0, f"Label #{label.id} on {image.path}\nLon: {label.lon:.6f}, Lat: {label.lat:.6f}")
        label_item.setIcon(0, style.standardIcon(QStyle.SP_FileIcon))
        group_item.addChild(label_item)

        # Update group check state if this label is visible
        if is_visible and group_item.checkState(0) != Qt.Checked:
            group_item.setCheckState(0, Qt.Checked)

        self.tree.blockSignals(False)

    def remove_label(self, label_id: int):
        """Remove a single label from the tree incrementally.

        Args:
            label_id: The ID of the label to remove
        """
        self.tree.blockSignals(True)

        # Find and remove the label item
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                if child.data(0, Qt.UserRole + 2) == label_id:
                    group_item.removeChild(child)

                    # Update or remove the group
                    remaining = group_item.childCount()
                    if remaining == 0:
                        self.tree.takeTopLevelItem(i)
                    else:
                        # Update label count and color
                        object_id = group_item.data(0, Qt.UserRole)
                        short_id = object_id[:8] + "..."
                        group_item.setText(0, f"Object: {short_id} ({remaining})")
                        if remaining == 1:
                            # Back to cornflower blue for single
                            group_item.setForeground(0, QColor(100, 149, 237))

                    self.tree.blockSignals(False)
                    return

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

            # If turning ON, ensure parent group is also checked
            if checked:
                parent = item.parent()
                if parent is not None and parent.checkState(0) != Qt.Checked:
                    self.tree.blockSignals(True)
                    parent.setCheckState(0, Qt.Checked)
                    self.tree.blockSignals(False)

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
            zoom_label_action.triggered.connect(
                lambda: self.zoom_to_label_requested.emit(lon, lat))

            # Zoom to layer
            layer_id = self._layer_id_map.get(file_path)
            if layer_id:
                zoom_layer_action = menu.addAction("Zoom to Layer")
                zoom_layer_action.triggered.connect(
                    lambda: self.zoom_to_layer_requested.emit(layer_id))
        elif item_type == "group":
            # Zoom to first label in this group
            if item.childCount() > 0:
                first_child = item.child(0)
                lon = first_child.data(0, Qt.UserRole + 3)
                lat = first_child.data(0, Qt.UserRole + 4)
                zoom_label_action = menu.addAction("Zoom to Label")
                zoom_label_action.triggered.connect(
                    lambda: self.zoom_to_label_requested.emit(lon, lat))

            menu.addSeparator()

            # Select/unselect all in group
            select_all_action = menu.addAction("Select all")
            select_all_action.triggered.connect(
                lambda: self._set_group_checked(item, True))

            unselect_all_action = menu.addAction("Unselect all")
            unselect_all_action.triggered.connect(
                lambda: self._set_group_checked(item, False))

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
        self.tree.blockSignals(True)

        def find_and_set(parent=None):
            if parent is None:
                count = self.tree.topLevelItemCount()
                for i in range(count):
                    find_and_set(self.tree.topLevelItem(i))
            else:
                item_type = parent.data(0, Qt.UserRole + 1)
                if item_type == "label":
                    if parent.data(0, Qt.UserRole) == file_path:
                        parent.setCheckState(
                            0, Qt.Checked if checked else Qt.Unchecked)
                        # If turning ON, also check the parent group
                        if checked:
                            group = parent.parent()
                            if group is not None and group.checkState(
                                    0) != Qt.Checked:
                                group.setCheckState(0, Qt.Checked)
                elif item_type == "group":
                    for i in range(parent.childCount()):
                        find_and_set(parent.child(i))

        find_and_set()
        self.tree.blockSignals(False)

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
        """Clear all items from the tree and internal state."""
        self.tree.clear()
        self._layer_id_map.clear()


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
        self.main_panel.layer_visibility_changed.connect(
            self._on_main_visibility_changed)
        self.main_panel.layers_reordered.connect(self.layers_reordered)
        self.main_panel.layer_group_changed.connect(self.layer_group_changed)
        self.main_panel.zoom_to_layer_requested.connect(
            self.zoom_to_layer_requested)
        self.main_panel.layer_removed.connect(self.layer_removed)

        # Forward batch progress signals
        self.main_panel.batch_visibility_started.connect(
            self.batch_visibility_started)
        self.main_panel.batch_visibility_progress.connect(
            self.batch_visibility_progress)
        self.main_panel.batch_visibility_finished.connect(
            self.batch_visibility_finished)

        # Forward signals from labeled panel
        self.labeled_panel.layer_visibility_changed.connect(
            self._on_labeled_visibility_changed)
        self.labeled_panel.zoom_to_layer_requested.connect(
            self.zoom_to_layer_requested)
        self.labeled_panel.zoom_to_label_requested.connect(
            self.zoom_to_label_requested)

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
    def add_layer(self, layer_id: str, file_path: str,
                  parent: QTreeWidgetItem = None, visible: bool = True):
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

    def add_group(self, name: str, parent: QTreeWidgetItem = None,
                  visible: bool = True):
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
        # Just toggle in main panel - the signal handler _on_main_visibility_changed
        # will automatically sync to the labeled panel
        self.main_panel.toggle_layer_visibility(layer_id)

    def get_checked_layers_in_selected_group(self) -> list[str]:
        """Get list of checked layer IDs within the currently selected group."""
        return self.main_panel.get_checked_layers_in_selected_group()

    def get_all_layers_in_selected_group(self) -> list[str]:
        """Get list of ALL layer IDs within the currently selected group."""
        return self.main_panel.get_all_layers_in_selected_group()

    def get_selected_group_name(self) -> str:
        """Get the name of the currently selected group."""
        return self.main_panel.get_selected_group_name()

    def clear(self):
        """Clear all items from both trees."""
        self.main_panel.clear()
        self.labeled_panel.clear()

    def refresh_labeled_panel(self, project):
        """Refresh the labeled images panel with current project data."""
        # Create a visibility checker that looks up layer visibility from main
        # panel
        def check_visibility(file_path: str) -> bool:
            layer_id = self.labeled_panel._layer_id_map.get(file_path)
            if layer_id:
                return self.main_panel.is_layer_checked(layer_id)
            return False

        self.labeled_panel.refresh(
            project, visibility_checker=check_visibility)

    def add_label_to_panel(self, label, image):
        """Add a single label to the labeled panel incrementally."""
        def check_visibility(file_path: str) -> bool:
            layer_id = self.labeled_panel._layer_id_map.get(file_path)
            if layer_id:
                return self.main_panel.is_layer_checked(layer_id)
            return False

        self.labeled_panel.add_label(label, image, visibility_checker=check_visibility)

    def remove_label_from_panel(self, label_id: int):
        """Remove a single label from the labeled panel incrementally."""
        self.labeled_panel.remove_label(label_id)

    @property
    def tree(self):
        """Access the main panel's tree widget for compatibility."""
        return self.main_panel.tree
