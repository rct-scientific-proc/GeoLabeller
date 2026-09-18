"""The Snippet Editor window: one list of snippets, two ways to work on them.

Holds the pieces of this package around ONE snippet list and ONE Show
filter:

  * Single view - one snippet at a time, zoomable: the mask section
    (mask_section.MaskEditor, hosted);
  * Grid view - a page of snippets at once: the orientation section's grid
    (orientation_section.OrientationEditor, hosted), laying out exactly
    what the list shows, in the list's order. Double-click a snippet there
    to open it in the Single view.

On the right, a column of SECTIONS - one per thing recorded about a
snippet, Orientation and Masks now, more to come - each under a header
that switches it on and off. Switching one off takes all of it away: its
panel, its pair on the Show filter ("Needs orientation", "Needs masks",
...), its overlay on the snippets and its tool. The save bar at the
bottom says where the work stands, as the mask editor's did.

The window talks to the rest of the app in the same terms the two editors
did - set_labels / set_mask_names / set_save_state in, masks_changed /
orientation_changed / mask_names_changed / save_requested out - so the
main window can hold it exactly as it held them.
"""
from PyQt5.QtCore import QEvent, QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QKeySequence
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QShortcut, QSplitter,
                             QStackedWidget, QVBoxLayout, QWidget)

from ..debug_log import debug
from .mask_section import MASK_WORKLIST, MaskEditor
from .orientation_section import (ORIENTATION_WORKLIST, OrientationEditor,
                                  OrientationPanel)
from .strip import SnippetStrip

_SETTINGS = ("GeoLabeller", "GeoLabeller")
_VIEW_KEY = "snippet_editor/view"
_SPLITTER_KEY = "snippet_editor/splitter"
_SECTIONS_KEY = "snippet_editor/sections"     # the sections switched ON


class SectionFrame(QWidget):
    """One section in the column: a header that switches it, its panel.

    Switched off, only the header stays - so it can be switched back on -
    and the window takes the rest of the section away (see _apply_sections).
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, content: QWidget, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        self.header = QCheckBox(title)
        font = QFont(self.header.font())
        font.setBold(True)
        self.header.setFont(font)
        self.header.setChecked(True)
        self.header.setToolTip(f"Switch the {title} section on or off.")
        self.header.toggled.connect(self._on_toggled)
        layout.addWidget(self.header)
        # A line under the header for when the section is present but
        # waiting (the Masks section in the Grid view).
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        self.content = content
        layout.addWidget(content)

    def is_on(self) -> bool:
        return self.header.isChecked()

    def set_on(self, on: bool):
        self.header.setChecked(bool(on))

    def set_note(self, text: str):
        self.note.setText(text)
        self.note.setVisible(bool(text) and self.is_on())

    def _on_toggled(self, on: bool):
        self.content.setVisible(on)
        self.note.setVisible(on and bool(self.note.text()))
        self.toggled.emit(on)


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
        self.orientation_panel = OrientationPanel(self.grid.propagate_check)
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

        # The sections, in the order they are offered. Each is a key, the
        # frame showing it, and what it gives the Show filter.
        self.sections = {
            "orientation": SectionFrame("Orientation",
                                        self.orientation_panel),
            "masks": SectionFrame("Masks", self.masks.mask_panel),
        }
        self._worklists = {"orientation": ORIENTATION_WORKLIST,
                           "masks": MASK_WORKLIST}
        column = QWidget()
        column_box = QVBoxLayout(column)
        for frame in self.sections.values():
            column_box.addWidget(frame)
        column_box.addStretch(1)
        self.section_column = QScrollArea()
        self.section_column.setWidgetResizable(True)
        self.section_column.setWidget(column)
        self.section_column.setMinimumWidth(190)
        self.body_splitter.addWidget(self.section_column)

        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setStretchFactor(2, 0)
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
        self.strip.entry_picked.connect(self.orientation_panel.show_entry)
        self.orientation_panel.clear_requested.connect(self.grid._on_clear)
        for frame in self.sections.values():
            frame.toggled.connect(self._apply_sections)
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
        single = view == self.SINGLE
        self.strip.panel.setVisible(single)
        # Masks are painted in the Single view. In the Grid the panel's
        # buttons would act on a snippet that is not on screen, so it waits.
        self.masks.mask_panel.setEnabled(single)
        self.sections["masks"].set_note(
            "" if single else "Masks are painted in the Single view - "
                              "double-click a snippet to open it there.")

    # -- sections -----------------------------------------------------------

    def sections_on(self) -> list:
        return [key for key, frame in self.sections.items() if frame.is_on()]

    def _apply_sections(self, *_args):
        """Give each section all of itself, or none of it."""
        on = self.sections_on()
        self.strip.set_worklists([self._worklists[key] for key in on])
        self.grid.set_orientation_shown("orientation" in on)
        self.masks.canvas.set_layers_shown("masks" in on)

    def _open_in_single_view(self, label_id: int):
        """A grid cell was double-clicked: look at it closer."""
        if self.strip.select(label_id):
            self.set_view(self.SINGLE)

    # -- traffic ------------------------------------------------------------

    def _on_orientation_changed(self, label_id, rad, deg, derived):
        # The grid edited the strip's own entry dict; recount and
        # recaption it, then pass the change on.
        self.strip.refresh(label_id)
        current = self.strip.current_entry()
        if current is not None and current["label_id"] == label_id:
            self.orientation_panel.show_entry(current)
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
            # Wide enough for "vessel . survey_04_r006_c112.tif" beside its
            # thumbnail; the old 210 cut captions off mid-name.
            self.body_splitter.setSizes([260, 780, 260])
        stored = settings.value(_SECTIONS_KEY)
        if stored is not None:
            wanted = [key for key in str(stored).split(",") if key]
            for key, frame in self.sections.items():
                frame.header.blockSignals(True)
                frame.set_on(key in wanted)
                frame.header.blockSignals(False)
                frame.content.setVisible(frame.is_on())
        self._apply_sections()

    def save_settings(self):
        """The view and pane widths - this window's and the mask panel's,
        which gets no close event of its own while hosted."""
        try:
            settings = QSettings(*_SETTINGS)
            settings.setValue(_VIEW_KEY, self.view())
            settings.setValue(_SPLITTER_KEY, self.body_splitter.saveState())
            settings.setValue(_SECTIONS_KEY, ",".join(self.sections_on()))
        except Exception as exc:                  # noqa: BLE001
            debug(f"snippet editor settings not saved: "
                  f"{type(exc).__name__}: {exc}")
        self.masks.save_settings()

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)
