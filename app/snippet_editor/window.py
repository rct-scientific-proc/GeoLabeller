"""The Snippet Editor window: one list of snippets, two ways to work on them.

Holds the pieces of this package around ONE snippet list and ONE Show
filter:

  * Single view - one snippet at a time, zoomable: the mask section
    (mask_section.MaskEditor, hosted);
  * Grid view - a page of snippets at once: the orientation section's grid
    (orientation_section.OrientationEditor, hosted), laying out exactly
    what the list shows, in the list's order. Double-click a snippet there
    to open it in the Single view.

The Show filter offers every section's worklist ("Needs orientation",
"Needs masks", ...), and the save bar at the bottom says where the work
stands, as the mask editor's did. The window talks to the rest of the app
in the same terms the two editors did - set_labels / set_mask_names /
set_save_state in, masks_changed / orientation_changed /
mask_names_changed / save_requested out - so the main window can hold it
exactly as it held them.
"""
from PyQt5.QtCore import QEvent, QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                             QShortcut, QSplitter, QStackedWidget,
                             QVBoxLayout, QWidget)

from ..debug_log import debug
from .mask_section import MASK_WORKLIST, MaskEditor
from .orientation_section import ORIENTATION_WORKLIST, OrientationEditor
from .strip import SnippetStrip

_SETTINGS = ("GeoLabeller", "GeoLabeller")
_VIEW_KEY = "snippet_editor/view"
_SPLITTER_KEY = "snippet_editor/splitter"


class SnippetEditor(QWidget):
    """One window for everything recorded per snippet."""

    # The same traffic the two editors carried, so the main window can
    # hold this exactly as it held them.
    masks_changed = pyqtSignal(int, list)
    orientation_changed = pyqtSignal(int, object, object, bool)
    mask_names_changed = pyqtSignal(list)
    save_requested = pyqtSignal()

    SINGLE, GRID = 0, 1

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Snippet Editor")
        self.strip = SnippetStrip(ORIENTATION_WORKLIST, self)
        self.strip.set_worklists([ORIENTATION_WORKLIST, MASK_WORKLIST])
        self.masks = MaskEditor(self, window=False, strip=self.strip)
        self.grid = OrientationEditor(self, window=False, strip=self.strip)
        self._setup_ui()
        self._wire()
        self._restore_settings()

    # -- layout -------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Class:"))
        toolbar.addWidget(self.strip.class_combo, 1)
        toolbar.addWidget(QLabel("Show:"))
        toolbar.addWidget(self.strip.filter_combo)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("View:"))
        self.single_button = QPushButton("Single")
        self.single_button.setToolTip(
            "One snippet at a time, zoomable - for painting masks.\n"
            "Space / Ctrl+Space step through the list.")
        self.grid_button = QPushButton("Grid")
        self.grid_button.setToolTip(
            "A page of snippets at once - for orienting them quickly.\n"
            "Double-click one to open it in the Single view.")
        self._view_buttons = QButtonGroup(self)
        for index, button in ((self.SINGLE, self.single_button),
                              (self.GRID, self.grid_button)):
            button.setCheckable(True)
            self._view_buttons.addButton(button, index)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        # The list, then whichever view is showing. The Grid view is a list
        # of its own, so it takes the list's pane when it is up.
        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.addWidget(self.strip.panel)
        self.views = QStackedWidget()
        self.views.addWidget(self.masks)          # SINGLE
        self.views.addWidget(self.grid)           # GRID
        self.body_splitter.addWidget(self.views)
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.body_splitter, 1)

        # Where the work stands: stored in the project as it happens, but
        # "stored" is not "on disk", and saying so plainly is the point.
        footer = QHBoxLayout()
        self.save_status = QLabel("")
        self.save_status.setWordWrap(True)
        footer.addWidget(self.save_status, 1)
        self.save_button = QPushButton("Save Project")
        self.save_button.setToolTip(
            "Write the project - orientations and masks included - to its "
            "file (Ctrl+S).")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        # The one Ctrl+S in this window - the hosted mask panel does not
        # claim it, since two claims of one chord make Qt fire neither.
        QShortcut(QKeySequence.Save, self,
                  activated=self.save_requested.emit)

    def _wire(self):
        self._view_buttons.idClicked.connect(self.set_view)
        self.save_button.clicked.connect(self.save_requested.emit)
        self.masks.masks_changed.connect(self.masks_changed)
        self.masks.mask_names_changed.connect(self.mask_names_changed)
        self.grid.orientation_changed.connect(self._on_orientation_changed)
        self.grid.open_requested.connect(self._open_in_single_view)
        # Space steps the list wherever the keyboard is in the window.
        self.strip.list_widget.installEventFilter(self)

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Every label as the entry dicts the two editors took."""
        self.strip.set_labels(entries)

    def set_mask_names(self, names: list):
        self.masks.set_mask_names(names)

    def set_save_state(self, text: str, saved: bool):
        """Show whether what has been done is on disk yet."""
        self.save_status.setText(text)
        self.save_status.setStyleSheet(
            "color: #2e7d32;" if saved else "color: #b26a00;")
        self.save_button.setEnabled(not saved)

    def save_status_text(self) -> str:
        return self.save_status.text()

    # -- views --------------------------------------------------------------

    def view(self) -> int:
        return self.views.currentIndex()

    def set_view(self, view: int):
        self.views.setCurrentIndex(view)
        self._view_buttons.button(view).setChecked(True)
        self.strip.panel.setVisible(view == self.SINGLE)

    def _open_in_single_view(self, label_id: int):
        """A grid cell was double-clicked: look at it closer."""
        if self.strip.select(label_id):
            self.set_view(self.SINGLE)

    # -- traffic ------------------------------------------------------------

    def _on_orientation_changed(self, label_id, rad, deg, derived):
        # The grid edited the strip's own entry dict; recount and
        # recaption it, then pass the change on.
        self.strip.refresh(label_id)
        self.orientation_changed.emit(label_id, rad, deg, derived)

    # -- keys ---------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.view() == self.SINGLE:
            self.strip.cycle(
                -1 if event.modifiers() & Qt.ControlModifier else 1)
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Steal Space from the list, so stepping works from there too."""
        if (event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Space):
            self.strip.cycle(
                -1 if event.modifiers() & Qt.ControlModifier else 1)
            return True
        return super().eventFilter(obj, event)

    # -- settings -----------------------------------------------------------

    def _restore_settings(self):
        settings = QSettings(*_SETTINGS)
        try:
            view = int(settings.value(_VIEW_KEY, self.SINGLE))
        except (TypeError, ValueError):
            view = self.SINGLE
        self.set_view(view if view in (self.SINGLE, self.GRID)
                      else self.SINGLE)
        saved = settings.value(_SPLITTER_KEY)
        restored = False
        if saved is not None:
            try:
                restored = self.body_splitter.restoreState(saved)
            except TypeError:
                pass        # something else wrote this key
        if not restored:
            self.body_splitter.setSizes([210, 900])

    def save_settings(self):
        """The view and pane widths - this window's and the mask panel's,
        which gets no close event of its own while hosted."""
        try:
            settings = QSettings(*_SETTINGS)
            settings.setValue(_VIEW_KEY, self.view())
            settings.setValue(_SPLITTER_KEY, self.body_splitter.saveState())
        except Exception as exc:                  # noqa: BLE001
            debug(f"snippet editor settings not saved: "
                  f"{type(exc).__name__}: {exc}")
        self.masks.save_settings()

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)
