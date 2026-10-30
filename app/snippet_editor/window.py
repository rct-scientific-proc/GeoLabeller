"""The Snippet Editor window: one list of snippets, two ways to work on them.

Holds the pieces of this package around ONE snippet list and ONE Show
filter:

  * Single view - one snippet at a time, zoomable (the mask editor,
    hosted), with the tools the sections own: Orient and Paint masks;
  * Grid view - a page of snippets at once (the orientation grid, hosted),
    laying out exactly what the list shows, in the list's order.
    Double-click a snippet there to open it in the Single view.

On the right, a column of SECTIONS - one per thing recorded about a
snippet - each under a header that switches it on and off. Switching one
off takes all of it away: its panel, its pair on the Show filter, its
overlay on the snippets, its tool and its keys. The save bar at the bottom
says where the work stands.

The window names its sections only where it builds them and where their
values leave for the main window; everything else loops over them
(section.py has the interface). The next value the ML team asks for is a
new module, one line in that list, and its outward signal.

It talks to the rest of the app through set_labels / set_classes /
set_mask_names / set_save_state in, and masks_changed /
orientation_changed / size_changed / confidence_changed / review_changed /
class_changed / position_changed / description_changed /
group_id_changed / mask_names_changed / remove_requested / save_requested /
undo_requested / redo_requested out.
"""
from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QKeySequence
from PyQt5.QtWidgets import (QGroupBox, QAbstractSpinBox, QApplication, QButtonGroup,
                             QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton,
                             QScrollArea, QShortcut, QSplitter,
                             QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from ..settings_scope import settings as app_settings
from ..debug_log import debug
from .confidence_section import ConfidenceSection
from .review_section import ReviewSection
from .notes_section import NotesSection
from .linked_row import LinkedRow
from .size_section import SizeSection
from .mask_section import MaskEditor, MaskSection
from .orientation_section import OrientationEditor, OrientationSection
from .single_view import TOOL_MOVE, TOOL_NONE, TOOL_ORIENT, TOOL_PAINT
from .strip import SnippetStrip

_VIEW_KEY = "snippet_editor/view"
_SPLITTER_KEY = "snippet_editor/splitter"
# The sections switched OFF - so a section added in a later release starts
# on for someone whose settings predate it.
_SECTIONS_OFF_KEY = "snippet_editor/sections_off"
_TOOL_KEY = "snippet_editor/tool"
_CLASS_LINKED_KEY = "snippet_editor/class_change_linked"
_GRID_MODE_KEY = "snippet_editor/grid_mode"


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

    # Out to the main window.
    masks_changed = pyqtSignal(int, list)
    orientation_changed = pyqtSignal(int, object, object, bool)
    confidence_changed = pyqtSignal(int, int)
    review_changed = pyqtSignal(int, str)
    # (label_id): the Review section asks for the label to be removed
    # from the project (Delete, or its button).
    remove_requested = pyqtSignal(int)
    # ([label ids], status): a page of the review grid marked at once.
    reviews_changed = pyqtSignal(list, str)
    # (label_id, class name): the main window applies it to the label's
    # whole linked object - the same thing seen in different images.
    class_changed = pyqtSignal(int, str, bool)   # +also the linked labels
    # ([label ids], class name): the Grid view's picked snippets, given
    # a class at once (2.9.16) - those labels only, never their linked
    # siblings; one undo step, and a new class when the name is new.
    classes_changed = pyqtSignal(list, str)
    # (label_id, pixel_x, pixel_y): the Move label tool - where on its
    # own image the label belongs.
    position_changed = pyqtSignal(int, float, float)
    # (label_id, text): this label's description; and the Group ID, which
    # the main window gives to the label's whole linked object.
    description_changed = pyqtSignal(int, str)
    group_id_changed = pyqtSignal(int, str)
    # (label_id, length_m or None, width_m or None)
    size_changed = pyqtSignal(int, object, object)
    mask_names_changed = pyqtSignal(list)
    save_requested = pyqtSignal()
    # Ctrl+Z / Ctrl+Y here undo and redo in the main window's one history,
    # so the order matches what was done across both windows.
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()

    SINGLE, GRID = 0, 1

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Snippet Editor")
        # The two views, around one list - which starts with no worklist;
        # the sections supply theirs once they exist.
        self.strip = SnippetStrip(None, self)
        self.masks = MaskEditor(self.strip, self)
        self.grid = OrientationEditor(self.strip, self)
        # The sections, in the order they are offered - in the column, on
        # the Show filter, and for tools on the Single view. The only place
        # the window names them.
        sections = (NotesSection(self),
                    OrientationSection(self.grid, self.masks, self),
                    MaskSection(self.masks, self),
                    SizeSection(self.masks, self.grid._geo_info, self),
                    ConfidenceSection(self),
                    ReviewSection(self.grid, self))
        self.section_objects = {section.key: section for section in sections}
        self.orientation_panel = self.section_objects["orientation"].panel
        self._apply_worklists(self.section_objects)
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
            "One snippet at a time, zoomable - for masks, orientation and\n"
            "rating. Space / Ctrl+Space step through the list.")
        self.grid_button = QPushButton("Grid")
        self.grid_button.setToolTip(
            "A page of snippets at once - for orienting them quickly, or\n"
            "reviewing a page at a time.\n"
            "Double-click one to open it in the Single view.")
        self._view_buttons = QButtonGroup(self)
        for index, button in ((self.SINGLE, self.single_button),
                              (self.GRID, self.grid_button)):
            button.setCheckable(True)
            self._view_buttons.addButton(button, index)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        # The Single view's tools, one per section that owns one, at the
        # front of that view's own row (beside Snippet and Brush) - the
        # toolbar above has no room for them on a 1366-pixel screen.
        tool_row = self.masks.tool_row
        tool_row.insertWidget(0, QLabel("Tool:"))
        self._tool_buttons = QButtonGroup(self)
        self._tools = {}                 # tool id -> button
        self._tool_owner = {}            # tool id -> section key, or None
        self._tool_keys = {}             # Qt key -> tool id
        self._tool_hints = {}            # tool id -> what it does
        tools = [(section.key,) + tuple(tool)
                 for section in self.section_objects.values()
                 for tool in section.tools]
        # And one that is no section's, so it is always there: the label
        # itself, which every section's values hang on.
        tools.append((None, TOOL_MOVE, "Move label",
                      "Click where the label belongs, and it moves there - "
                      "on this image.\nMasks, orientation and size stay "
                      "with the imagery they were drawn on.", Qt.Key_V))
        for owner, tool_id, text, tip, key in tools:
            # A tool is a key away, so a hand on the mouse never has to
            # leave the snippet for the row above it - and the key is on
            # the button, where it will be found.
            letter = QKeySequence(key).toString()
            button = QPushButton(f"{text} ({letter})")
            button.setToolTip(f"{tip}\n(key {letter})")
            self._tool_keys[key] = tool_id
            self._tool_hints[tool_id] = tip
            button.setCheckable(True)
            self._tool_buttons.addButton(button)
            tool_row.insertWidget(len(self._tools) + 1, button)
            self._tools[tool_id] = button
            self._tool_owner[tool_id] = owner
        tool_row.insertSpacing(len(self._tools) + 1, 16)
        self.paint_button = self._tools.get(TOOL_PAINT)
        self.orient_button = self._tools.get(TOOL_ORIENT)
        self.move_button = self._tools[TOOL_MOVE]

        # The list, then whichever view is showing. The Grid view is a list
        # of its own, so it takes the list's pane when it is up.
        self.body_splitter = QSplitter(Qt.Horizontal)
        self.body_splitter.addWidget(self.strip.panel)
        self.views = QStackedWidget()
        # The Single view: the snippet in hand, and under it every snippet
        # of the object it belongs to (hidden for a label that is alone).
        self.linked_row = LinkedRow()
        self.linked_row.set_badges(
            lambda entry: "".join(w.badge(entry)
                                  for w in self.strip._worklists))
        self.linked_row.picked.connect(self._pick_linked)
        single_page = QWidget()
        single_box = QVBoxLayout(single_page)
        single_box.setContentsMargins(0, 0, 0, 0)
        single_box.addWidget(self.masks, 1)
        single_box.addWidget(self.linked_row)
        self.views.addWidget(single_page)         # SINGLE
        self.views.addWidget(self.grid)           # GRID
        self.body_splitter.addWidget(self.views)

        # The column of sections, under the snippet's class: a correction
        # noticed while reviewing is made here, not back on the canvas.
        self.sections = {key: SectionFrame(section.title, section.panel)
                         for key, section in self.section_objects.items()}
        column = QWidget()
        column_box = QVBoxLayout(column)
        class_box = QGroupBox("Label class")
        class_layout = QVBoxLayout(class_box)
        self.label_class_combo = QComboBox()
        # Editable: a name that is not a class yet becomes one - for the
        # part of an object that turns out to need a label of its own.
        self.label_class_combo.setEditable(True)
        self.label_class_combo.setInsertPolicy(QComboBox.NoInsert)
        self.label_class_combo.setToolTip(
            "The class of the snippet in hand. Pick one, or type a new name "
            "and press Enter to create it (Ctrl+1-9 in the Single view).")
        # A class picked from the list, or a name entered, is a choice
        # made: the keys go back to the window, so Space steps again
        # rather than typing into the class.
        self.label_class_combo.activated.connect(self._on_class_activated)
        self.label_class_combo.lineEdit().returnPressed.connect(
            lambda: self._class_chosen(
                self.label_class_combo.currentText().strip()))
        # Qt's combo also emits activated when the box loses focus with
        # text matching an item: the second of two changes on Enter (the
        # first stepped the strip, so the second re-classed the NEXT
        # snippet), and a class applied by clicking away from a name the
        # user never chose (audit 2026-10-28 H4, 2.9.12). The filter sees
        # the focus-out first and the activation it brings is left alone.
        # The combo itself holds the focus (the box is no focus proxy)
        # and hands the focus-out on to the box: filtered on both.
        self.label_class_combo.installEventFilter(self)
        self.label_class_combo.lineEdit().installEventFilter(self)
        class_layout.addWidget(self.label_class_combo)
        self.class_linked_check = QCheckBox("Also change linked labels")
        self.class_linked_check.setToolTip(
            "Give every label linked to this one the same class - they are "
            "the same object. Untick to change only this snippet.")
        # Ticked unless the user unticked it last time (_restore_settings).
        class_layout.addWidget(self.class_linked_check)
        class_hint = QLabel("Ctrl+1-9 picks a class by its place in the "
                            "list; a new name typed here becomes a class.")
        class_hint.setWordWrap(True)
        class_hint.setStyleSheet("color: palette(mid);")
        class_layout.addWidget(class_hint)
        column_box.addWidget(class_box)
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
            "Write the project - everything recorded here included - to "
            "its file (Ctrl+S).")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        # The one Ctrl+S in this window - the hosted mask panel does not
        # claim it, since two claims of one chord make Qt fire neither.
        QShortcut(QKeySequence.Save, self, activated=self._request_save)

    def _wire(self):
        self._view_buttons.idClicked.connect(self.set_view)
        self.save_button.clicked.connect(self._request_save)
        for tool_id, button in self._tools.items():
            button.clicked.connect(
                lambda _checked=False, t=tool_id: self.set_tool(t))
        # Out to the main window.
        self.masks.masks_changed.connect(self.masks_changed)
        self.masks.mask_names_changed.connect(self.mask_names_changed)
        self.masks.label_moved.connect(self.position_changed)
        self.grid.orientation_changed.connect(self.orientation_changed)
        self.grid.classes_requested.connect(self.classes_changed)
        self.section_objects["confidence"].confidence_changed.connect(
            self.confidence_changed)
        self.section_objects["size"].size_changed.connect(self.size_changed)
        review = self.section_objects["review"]
        review.review_changed.connect(self.review_changed)
        review.reviews_changed.connect(self.reviews_changed)
        review.remove_requested.connect(self.remove_requested)
        notes = self.section_objects["notes"]
        notes.description_changed.connect(self.description_changed)
        notes.group_id_changed.connect(self.group_id_changed)
        # Between the sections.
        self.grid.open_requested.connect(self._open_in_single_view)
        # After the Single view has put a snippet on its canvas - and knows
        # its crop - so a section drawing there lands on the right pixel.
        self.masks.snippet_shown.connect(self._show_entry)
        for section in self.section_objects.values():
            section.entry_changed.connect(self._on_entry_changed)
            section.entries_changed.connect(self._on_entries_changed)
            # A key that finishes the snippet moves on to the next, when
            # its section asks.
            section.advance_requested.connect(lambda: self.strip.cycle(1))
        for frame in self.sections.values():
            frame.toggled.connect(self._apply_sections)
        # Space steps the list wherever the keyboard is in the window.
        QApplication.instance().installEventFilter(self)

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Every label, as the entry dicts the main window builds."""
        self.strip.set_labels(entries)

    def set_mask_names(self, names: list):
        self.masks.set_mask_names(names)

    def set_classes(self, names: list):
        """The project's classes, in order, for the Label class picker -
        and the Grid view's, for the picked snippets."""
        self._classes = list(names)
        self._show_class(self.strip.current_entry())
        self.grid.set_classes(self._classes)
        self.strip.set_known_classes(self._classes)

    def _show_class(self, entry):
        combo = self.label_class_combo
        combo.blockSignals(True)
        combo.clear()
        classes = list(getattr(self, "_classes", []))
        current = entry["class_name"] if entry is not None else ""
        if current and current not in classes:
            classes.append(current)          # a class since removed
        combo.addItems(classes)
        combo.setCurrentText(current)
        combo.setEnabled(entry is not None and self.view() == self.SINGLE)
        combo.blockSignals(False)

    _class_focus_out = False        # the class box just lost focus

    def _on_class_activated(self, index: int):
        """A pick from the list - not the activation a focus-out brings."""
        if not self._class_focus_out:
            self._class_chosen(self.label_class_combo.itemText(index))

    def _class_chosen(self, class_name: str):
        # clearFocus() brings a focus-out, and with it the activation
        # _on_class_activated leaves alone: one change, not two.
        self.label_class_combo.clearFocus()
        self.change_class(class_name)

    def _class_focus_out_over(self):
        self._class_focus_out = False

    def change_class(self, class_name: str):
        """Give the snippet in hand (and its linked object) ``class_name``.

        When the list is showing one class and this takes the snippet out
        of it, the editor moves on to the next snippet first, as a review
        pass would expect, rather than jumping back to the top.
        """
        entry = self.strip.current_entry()
        if entry is None or not class_name or \
                class_name == entry["class_name"]:
            return
        label_id = entry["label_id"]
        shown = self.strip.shown_class()
        if shown is not None and shown != class_name:
            self.strip.cycle(1)
        self.class_changed.emit(label_id, class_name,
                                self.class_linked_check.isChecked())

    def redraw_snippets(self):
        """Display Settings changed: read every snippet again, keeping the
        class, the filter and the snippet in hand."""
        self.strip._rebuild(chosen_anew=False)
        # The row sees the same object and would only recaption it.
        self.linked_row.redraw()

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
        self.grid.set_active(not single)
        self.strip.panel.setVisible(single)
        self.strip.current_in_hand = single
        # A class is changed on the snippet in hand, which only the Single
        # view has.
        self._show_class(self.strip.current_entry())
        for key, section in self.section_objects.items():
            self.sections[key].set_note(section.view_changed(single))

    def _open_in_single_view(self, label_id: int):
        """A grid cell was double-clicked: look at it closer."""
        if self.strip.select(label_id):
            self.set_view(self.SINGLE)

    def _pick_linked(self, label_id: int):
        """A snippet clicked in the linked row: put it in hand.

        The row shows the whole object, but the list may not: a view of
        it in another class, or one the Show filter leaves out, is not
        listed - and a click that did nothing read as broken. So the list
        is switched to show it: the Class dropdown to the object, the
        filter to All, only as far as needed.
        """
        if self.strip.select(label_id):
            return
        entry = self.strip.entry(label_id)
        if entry is None:
            return
        if not self.strip.in_class(entry):
            self.strip.show_object(entry.get("object_id"))
        if not self.strip.matches(entry):
            self.strip.set_filter_mode(self.strip.FILTER_ALL)
        self.strip.select(label_id)

    # -- sections -----------------------------------------------------------

    def sections_on(self) -> list:
        return [key for key, frame in self.sections.items() if frame.is_on()]

    def _apply_sections(self, *_args):
        """Give each section all of itself, or none of it."""
        on = self.sections_on()
        self._apply_worklists(on)
        for key, section in self.section_objects.items():
            section.set_shown(key in on)
        # Each tool goes with its section; if the one in hand went - or
        # none is in hand - take up the first section tool that is left,
        # and with none left hold nothing. Move label is never taken up
        # unasked: a tool that moves a label at a click is one to pick
        # up on purpose, and "the first that is left" used to be it.
        for tool_id, button in self._tools.items():
            owner = self._tool_owner[tool_id]
            button.setEnabled(owner is None or owner in on)
        in_hand = self._tools.get(self.tool())
        if in_hand is None or not in_hand.isEnabled():
            left = [tool_id for tool_id, button in self._tools.items()
                    if button.isEnabled()
                    and self._tool_owner[tool_id] is not None]
            self.set_tool(left[0] if left else TOOL_NONE)

    def _apply_worklists(self, keys):
        """The Show filter's pairs: one for each of these sections that
        has something to be done."""
        self.strip.set_worklists([
            self.section_objects[key].worklist for key in keys
            if self.section_objects[key].worklist is not None])

    def _show_entry(self, entry):
        self._show_class(entry)
        self.linked_row.show_for(entry, self.strip.entries)
        for section in self.section_objects.values():
            section.show_entry(entry)

    def _on_entry_changed(self, label_id: int):
        """A section edited a label: recount the list, let every section
        redisplay it."""
        self.strip.refresh(label_id)
        self.linked_row.refresh()
        for section in self.section_objects.values():
            section.refresh(label_id)

    def _on_entries_changed(self, label_ids: list):
        """A section edited many labels at once: recount the list once."""
        self.strip.refresh_many(label_ids)
        self.linked_row.refresh()
        for section in self.section_objects.values():
            for label_id in label_ids:
                section.refresh(label_id)

    def _request_save(self):
        # A mark the review grid has shown but not yet stored goes in.
        self.grid.flush_review()
        self.save_requested.emit()

    # -- tools --------------------------------------------------------------

    def tool(self) -> str:
        return self.masks.canvas.tool()

    def set_tool(self, tool: str):
        """A tool id from a section's ``tool``, for the Single view - or
        TOOL_NONE, nothing in hand, when no section with a tool is on."""
        self.masks.canvas.set_tool(tool)
        button = self._tools.get(tool)
        if button is not None:
            button.setChecked(True)
        else:
            # An exclusive group lets no button go unchecked.
            checked = self._tool_buttons.checkedButton()
            if checked is not None:
                self._tool_buttons.setExclusive(False)
                checked.setChecked(False)
                self._tool_buttons.setExclusive(True)
        self.masks.set_tool_hint(self._tool_hints.get(
            tool, "No tool in hand: switch Masks, Orientation or Size on "
            "for one, or pick Move label (V)."))

    # -- keys ---------------------------------------------------------------

    def keyPressEvent(self, event):
        if not self._take_key(event):
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Claim this window's keys before the focused widget eats them.

        Qt hands a key to whichever widget holds focus, so which keys
        worked depended on the last thing clicked: Add Mask kept the focus
        and Space pressed it again, the snippet list took 1-5 as
        type-ahead, and the paint canvas takes no focus at all, so
        clicking the snippet being painted never helped. Reported from the
        field as being stuck in the paintbrush (2026-09-20).

        A filter on the application sees every key first; this answers for
        the ones aimed at this window.
        """
        if (event.type() == QEvent.KeyPress and self._owns(obj)
                and self._take_key(event)):
            return True
        if event.type() == QEvent.KeyRelease and self._owns(obj):
            self._key_released(event)
        if (event.type() == QEvent.FocusOut
                and (obj is self.label_class_combo
                     or obj is self.label_class_combo.lineEdit())
                and event.reason() != Qt.PopupFocusReason):
            # The activation this focus-out brings is not a choice (see
            # _on_class_activated); the flag outlives the event by one
            # turn of the loop, which is as long as Qt takes to emit it.
            self._class_focus_out = True
            QTimer.singleShot(0, self._class_focus_out_over)
        return super().eventFilter(obj, event)

    def _key_released(self, event):
        """A key let go, for a section holding something on while it was
        down (H: a look under the masks). Told whatever has the focus by
        now, and never claimed - the focused widget gets its release too.
        The releases a held key repeats are not the key being let go."""
        if event.isAutoRepeat():
            return
        for section in self.section_objects.values():
            section.key_released(event.key())

    def changeEvent(self, event):
        # A key held as the window loses the keyboard is let go somewhere
        # this window never hears of: end what it was holding on.
        if (event.type() == QEvent.ActivationChange
                and not self.isActiveWindow()):
            self.masks.canvas.set_peek(False)
        super().changeEvent(event)

    def _owns(self, obj) -> bool:
        """Is this event on its way to something in this window?"""
        return isinstance(obj, QWidget) and (obj is self
                                             or self.isAncestorOf(obj))

    def _take_key(self, event) -> bool:
        """Step the list, or hand the key to a section that wants it."""
        focus = QApplication.focusWidget()
        # A mask name is typed, and may hold spaces and digits ("bow
        # wave"), so a text field keeps every key it is given - its own
        # Ctrl+Z included.
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)) or (
                isinstance(focus, QComboBox) and focus.isEditable()):
            return False
        if self._take_history_key(event):
            return True
        if self.view() != self.SINGLE:
            # The Grid view has keys of its own (A accepts a page, in
            # Review mode; Escape unpicks) - plain ones, like the
            # sections' - and Ctrl+A, which picks the page (2.9.16).
            mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier
                                        | Qt.ShiftModifier)
            if event.key() == Qt.Key_A and mods == Qt.ControlModifier:
                self.grid.select_all()
                return True
            if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False
            return self.grid.key_pressed(event.key())
        if self._take_class_key(event):
            return True
        if event.key() == Qt.Key_Space:
            self.strip.cycle(
                -1 if event.modifiers() & Qt.ControlModifier else 1)
            return True
        # Digits in a number box are the value being typed, and the keys
        # that edit it stay with it; a letter means nothing to a number
        # box, and is a section's or a tool's as anywhere else. (Space
        # above is claimed there too: a number box has no use for one.)
        if isinstance(focus, QAbstractSpinBox) and self._edits_a_number(
                event):
            return False
        # Sections answer to plain keys: Ctrl+A is not "accept", Ctrl+1 is
        # not a rating.
        if (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
                and not self._typed_with_altgr(event)):
            return False
        # A tool's key picks it up - if it is there to pick up: a tool
        # goes with its section.
        tool = self._tool_keys.get(event.key())
        if tool is not None and self._tools[tool].isEnabled():
            self.set_tool(tool)
            return True
        if event.key() == Qt.Key_C:
            # The context window, on or off (2.9.16): the view's, not a
            # section's, so it answers whatever sections are on. Not
            # with a shape being clicked out: another window to show
            # would drop it (2.9.17).
            if not self.masks.canvas.has_polygon():
                self.masks.toggle_context()
            return True
        if event.key() == Qt.Key_Delete and event.isAutoRepeat():
            # Delete removes the label in hand: held down, it removed one
            # per repeat, each its own undo step (2.9.17). The first
            # press is the one meant.
            return True
        for key in self.sections_on():
            if self.section_objects[key].key_pressed(event.key()):
                return True
        return False

    @staticmethod
    def _edits_a_number(event) -> bool:
        """Would a number box do something with this key?"""
        return event.text().isdigit() or event.key() in (
            Qt.Key_Minus, Qt.Key_Plus, Qt.Key_Period, Qt.Key_Comma,
            Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Left, Qt.Key_Right,
            Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp,
            Qt.Key_PageDown, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape,
            Qt.Key_Tab)

    @staticmethod
    def _typed_with_altgr(event) -> bool:
        """Is this a character that takes AltGr to type on this keyboard?
        [ and ] do on most European layouts, and Windows reports AltGr as
        Ctrl+Alt - which would otherwise make them chords, and dead here.
        A real Ctrl+Alt chord types no character of its own."""
        both = Qt.ControlModifier | Qt.AltModifier
        return ((event.modifiers() & both) == both
                and event.text() in ("[", "]"))

    def _take_class_key(self, event) -> bool:
        """Ctrl+1-9: the class at that place in the list."""
        mods = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier
                                    | Qt.AltModifier)
        if mods != Qt.ControlModifier or not \
                Qt.Key_1 <= event.key() <= Qt.Key_9:
            return False
        index = event.key() - Qt.Key_1
        classes = getattr(self, "_classes", [])
        if index < len(classes):
            self.change_class(classes[index])
        return True

    def _take_history_key(self, event) -> bool:
        """Ctrl+Z undoes; Ctrl+Y or Ctrl+Shift+Z redoes - in either view."""
        mods = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier
                                    | Qt.AltModifier)
        if event.key() == Qt.Key_Z and mods == Qt.ControlModifier:
            # What is undone is the last thing done - a mark the review
            # grid has shown but not yet stored included.
            self.grid.flush_review()
            self.undo_requested.emit()
            return True
        if (event.key() == Qt.Key_Y and mods == Qt.ControlModifier) or (
                event.key() == Qt.Key_Z
                and mods == Qt.ControlModifier | Qt.ShiftModifier):
            self.grid.flush_review()
            self.redo_requested.emit()
            return True
        return False

    # -- settings -----------------------------------------------------------

    def _restore_settings(self):
        settings = app_settings()
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
        off = {key for key in str(settings.value(_SECTIONS_OFF_KEY, ""))
               .split(",") if key}
        for key, frame in self.sections.items():
            frame.header.blockSignals(True)
            frame.set_on(key not in off)
            frame.header.blockSignals(False)
            frame.content.setVisible(frame.is_on())
        # Not Move label: a tool that moves a label at a click is one to
        # pick up on purpose, not to find in hand the next morning.
        tool = settings.value(_TOOL_KEY, TOOL_PAINT)
        self.set_tool(tool if tool in self._tools and tool != TOOL_MOVE
                      else TOOL_PAINT)
        self.class_linked_check.setChecked(
            str(settings.value(_CLASS_LINKED_KEY, "true")).lower() != "false")
        for section in self.section_objects.values():
            section.restore_settings(settings)
        self._apply_sections()
        # After the sections: Review mode needs the Review section on.
        self.grid.set_mode(str(settings.value(_GRID_MODE_KEY, "")))

    def save_settings(self):
        """The view, panes, sections and tool - and the mask panel's, which
        gets no close event of its own while hosted."""
        try:
            settings = app_settings()
            settings.setValue(_VIEW_KEY, self.view())
            settings.setValue(_SPLITTER_KEY, self.body_splitter.saveState())
            settings.setValue(_SECTIONS_OFF_KEY, ",".join(
                key for key in self.sections if key not in self.sections_on()))
            settings.setValue(_TOOL_KEY, self.tool())
            settings.setValue(_GRID_MODE_KEY, self.grid.mode())
            settings.setValue(_CLASS_LINKED_KEY, "true"
                              if self.class_linked_check.isChecked()
                              else "false")
            for section in self.section_objects.values():
                section.save_settings(settings)
        except Exception as exc:                  # noqa: BLE001
            debug(f"snippet editor settings not saved: "
                  f"{type(exc).__name__}: {exc}")
        self.masks.save_settings()

    def closeEvent(self, event):
        self.grid.flush_review()
        self.save_settings()
        # Nothing read for a closed window (2.9.13); the pools serve
        # again when it is shown again.
        for loader in (self.strip.loader, self.grid._loader,
                       self.linked_row.loader):
            loader.shutdown()
        super().closeEvent(event)
