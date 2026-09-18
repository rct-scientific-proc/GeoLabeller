"""The mask section: paint named binary masks onto label snippets.

Part of the Snippet Editor package (see app/snippet_editor/__init__.py):
MaskEditor is the window's Single view, and MaskSection the Masks section
of its column. Pick a snippet from the strip, add a named mask, and paint:
left-drag paints, right-drag erases, the brush size is adjustable, and
several masks can coexist on one snippet - each an independent binary
layer with its own overlay colour, kept from overlapping one another
unless Allow Overlap is on.

The paint surface itself is single_view.MaskPaintCanvas; the snippet list
is strip.SnippetStrip, which the window owns and places. This module is
what makes them a mask editor: the mask list, add/delete/fill, the overlap
toggle, the statistics, and committing every stroke to the label.
(Through 1.x this was a window of its own, Labels > Mask Editor.)

Masks are stored on the label as FULL-IMAGE run-length encodings (format
4.0): the painted window is spliced into the stored runs arithmetically,
so the entry is co-registered with its imagery and no image-sized array
is ever built - see app/masks.py for the exact format. Every stroke
re-encodes and emits immediately, so the project (and its autosave) is
never behind the screen. If an image's dimensions cannot be read, the
editor falls back to the 3.9 windowed anchoring, which readers still
honor.

The stats line is the point of the exercise: per-band mean +/- std of the
active mask's pixels versus the background, computed from RAW source
values, never from the display stretch.
"""
import numpy as np

from PyQt5.QtCore import QEvent, QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSlider,
    QSpinBox, QVBoxLayout, QWidget)

from ..debug_log import debug
from ..masks import (entry_in_window, fill_enclosed, mask_statistics,
                     merged_entry)
from ..snippets import (read_label_snippet, read_label_window_raw,
                        snippet_frame)
from .section import Section
from .single_view import (DEFAULT_BRUSH_PX, MASK_COLORS,
                          MASK_SNIPPET_SIZE, TOOL_PAINT, MaskPaintCanvas,
                          apply_display_adjust)
from .strip import SnippetStrip, Worklist


def _mask_badge(entry: dict) -> str:
    n = len(entry.get("masks") or [])
    return f"  [{n} mask{'s' if n > 1 else ''}]" if n else ""


# The mask editor's worklist for the shared snippet strip: a snippet is done
# once it carries at least one mask.
MASK_WORKLIST = Worklist(
    needs="Needs masks", done="Masked",
    is_done=lambda entry: bool(entry.get("masks")),
    badge=_mask_badge,
    nothing_left="All of these snippets have a mask. Nothing left to do.",
    none_done="No snippet here has a mask yet.")

# Where Allow Overlap is remembered. The key keeps the name it had when
# this was the Mask Editor window, so the choice carried over to 2.0.0.
_SETTINGS = ("GeoLabeller", "GeoLabeller")
_OVERLAP_KEY = "mask_editor/allow_overlap"


class MaskEditor(QWidget):
    """The Single view: paint named binary masks on the strip's snippets.

    A panel around the host's strip. Its mask panel (``mask_panel``) is
    not laid out here: it is the Masks section in the host's column.
    """

    # (label_id, [mask entries]) - the label's full replacement mask list.
    masks_changed = pyqtSignal(int, list)
    # The project's mask-name presets, after a typed name joined them.
    mask_names_changed = pyqtSignal(list)
    # A snippet was put on the canvas (or None) - after its frame is known,
    # so a host drawing on the canvas can place things in it.
    snippet_shown = pyqtSignal(object)

    def __init__(self, strip: SnippetStrip, parent=None):
        super().__init__(parent)
        # The snippet list, its filters and their counts (strip.py) - the
        # host's: it places them, and owns the save bar and Ctrl+S. This is
        # the paint canvas and the mask panel for the host's column.
        self.strip = strip
        self._mask_names: list = []              # the project's presets
        self._current: "dict | None" = None      # selected entry
        self._frame = (0, 0, MASK_SNIPPET_SIZE, MASK_SNIPPET_SIZE)
        # The as-stored entries, by name: committing a stroke merges the
        # edited window into these, so mask content lying OUTSIDE the
        # current window (painted earlier at a larger size) survives.
        self._stored_by_name: dict = {}
        self._layers: "dict[str, np.ndarray]" = {}
        self._order: list = []
        self._raw = None                          # (bands, h, w) source data
        # The snippet's display pixels as read, before brightness and
        # contrast. Kept so moving a slider is a redraw rather than a
        # re-read of the file.
        self._display_source = None
        self._raw_nodata = None                   # its declared nodata
        # Image (width, height) by path - one header read each, so every
        # stroke can serialize against the full image without touching disk.
        self._image_dims: "dict[str, tuple | None]" = {}
        self._setup_ui()
        self.strip.entry_picked.connect(self._show_entry)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        controls = self.tool_row = QHBoxLayout()
        controls.addWidget(QLabel("Snippet:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(16, 2048)
        self.size_spin.setSingleStep(32)
        self.size_spin.setValue(MASK_SNIPPET_SIZE)
        self.size_spin.setSuffix(" px")
        # Any typed value is accepted; committing on Enter/focus-out (not
        # per keystroke) stops "3" and "30" reloading on the way to "300".
        self.size_spin.setKeyboardTracking(False)
        self.size_spin.setToolTip(
            "Snippet size in SOURCE pixels around the label. Masks are\n"
            "anchored to the imagery, so changing size only changes the\n"
            "window you paint in - existing masks stay where they are,\n"
            "including any part outside the current view.")
        self.size_spin.valueChanged.connect(self._on_size_changed)
        controls.addWidget(self.size_spin)
        controls.addWidget(QLabel("Brush:"))
        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(1, 64)
        self.brush_spin.setValue(DEFAULT_BRUSH_PX)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.setToolTip("Brush diameter in source pixels.")
        controls.addWidget(self.brush_spin)
        # Nothing on this row wants the spare width (the class picker is on
        # the window's toolbar); without this the number boxes take it.
        controls.addStretch(1)
        layout.addLayout(controls)

        # Display-only brightness and contrast: sonar and low-light aerial
        # snippets can be nearly flat on screen, and an edge you cannot see
        # is an edge you cannot paint.
        #
        # On a row of their own. Qt will not shrink a window below the
        # widest row's minimum, and hanging these off the toolbar took
        # that to 1404 px - off the edge of a 1366-wide laptop screen,
        # with no way to drag it back. A strip of height is affordable
        # where width is not.
        controls = self.view_row = QHBoxLayout()
        controls.addWidget(QLabel("Bright:"))
        self.brightness_slider = QSlider(Qt.Horizontal)
        self.brightness_slider.setRange(-100, 100)
        self.brightness_slider.setValue(0)
        self.brightness_slider.setFixedWidth(90)
        self.brightness_slider.setToolTip(
            "Brightens the VIEW only. Masks and the object/background\n"
            "statistics come from the raw imagery and do not change.")
        self.brightness_slider.valueChanged.connect(
            self._apply_display_to_canvas)
        controls.addWidget(self.brightness_slider)
        controls.addWidget(QLabel("Contrast:"))
        self.contrast_slider = QSlider(Qt.Horizontal)
        self.contrast_slider.setRange(-100, 100)
        self.contrast_slider.setValue(0)
        self.contrast_slider.setFixedWidth(90)
        self.contrast_slider.setToolTip(
            "Stretches the VIEW around mid-grey. Masks and the\n"
            "object/background statistics do not change.")
        self.contrast_slider.valueChanged.connect(
            self._apply_display_to_canvas)
        controls.addWidget(self.contrast_slider)
        self.reset_adjust_button = QPushButton("Reset")
        self.reset_adjust_button.setToolTip(
            "Back to the imagery as it is.")
        self.reset_adjust_button.clicked.connect(self.reset_display_adjust)
        controls.addWidget(self.reset_adjust_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        hint = QLabel("Left-drag paints the active mask, right-drag erases; "
                      "wheel zooms (to the cursor), Shift+drag pans. "
                      "Space / Ctrl+Space step through the snippets. "
                      "Each mask is its own layer; masks do not overlap "
                      "unless Allow Overlap is on.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # The paint surface, scrollable so any zoom level fits on screen.
        self.canvas = MaskPaintCanvas()
        self.canvas.stroke_finished.connect(self._on_stroke_finished)
        self.brush_spin.valueChanged.connect(self.canvas.set_brush)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.setWidgetResizable(False)
        self.canvas_scroll.setAlignment(Qt.AlignCenter)
        self.canvas_scroll.setMinimumSize(360, 360)
        layout.addWidget(self.canvas_scroll, 1)

        # Mask management + statistics: the host's Masks section.
        self.mask_panel = QWidget()
        self.mask_panel.setMinimumWidth(150)
        side = QVBoxLayout(self.mask_panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(QLabel("Masks on this snippet:"))
        self.mask_list = QListWidget()
        self.mask_list.currentItemChanged.connect(self._on_mask_picked)
        self.mask_list.installEventFilter(self)
        side.addWidget(self.mask_list)

        # The ACTIVE mask name, the way the main window holds an active
        # class: set once, then worked down the strip. The list never
        # shrinks as masks are added - an earlier cut offered only names
        # the snippet lacked, so the presets appeared to be used up.
        # Editable, so a name that is not in the list yet is typed here
        # rather than in a prompt, and joins the presets on use.
        side.addWidget(QLabel("Mask name to add:"))
        self.mask_name_combo = QComboBox()
        self.mask_name_combo.setEditable(True)
        self.mask_name_combo.setInsertPolicy(QComboBox.NoInsert)
        self.mask_name_combo.setToolTip(
            "The name Add Mask gives the next mask. Type a new one, or\n"
            "pick a name already used in this project; choosing a name\n"
            "this snippet already has selects that mask to paint.")
        self.mask_name_combo.currentIndexChanged.connect(
            self._on_active_mask_name_picked)
        side.addWidget(self.mask_name_combo)
        # Why Add Mask did nothing, when it did nothing - under the name it
        # is about, not in a box that stops the run of snippets. It goes as
        # soon as the name is touched.
        self.name_note = QLabel("")
        self.name_note.setWordWrap(True)
        self.name_note.setStyleSheet("color: #b26a00;")
        self.name_note.hide()
        self.mask_name_combo.editTextChanged.connect(
            lambda _text: self._set_name_note(""))
        side.addWidget(self.name_note)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add Mask")
        self.add_button.setToolTip(
            "Add a mask with the name above and start painting it.")
        self.add_button.clicked.connect(self._on_add_mask)
        buttons.addWidget(self.add_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._on_delete_mask)
        buttons.addWidget(self.delete_button)
        side.addLayout(buttons)
        self.fill_button = QPushButton("Fill Enclosed")
        self.fill_button.setToolTip(
            "Draw a closed outline with a thin brush, then fill its\n"
            "inside in one click. Refused when the outline has a gap\n"
            "(nothing is actually enclosed).")
        self.fill_button.clicked.connect(self._on_fill_enclosed)
        tools = QHBoxLayout()
        tools.addWidget(self.fill_button)

        # Whether masks may share pixels. Off by default, by request:
        # a hull and its shadow are exclusive regions, and with it off
        # the other masks are walls - a stroke stops at them, and Fill
        # Enclosed treats them as edges, so a shadow drawn up to the
        # hull is closed by the hull. Remembered per user, not stored in
        # the project: it is a way of working, not data.
        self.overlap_button = QPushButton("Allow Overlap")
        self.overlap_button.setCheckable(True)
        self.overlap_button.setChecked(self._remembered_overlap())
        self.overlap_button.setToolTip(
            "On: masks may be painted over each other.\n"
            "Off: a stroke stops at other masks' pixels, and Fill\n"
            "Enclosed treats them as edges - draw the shadow up to the\n"
            "hull and the hull closes it.")
        self.overlap_button.toggled.connect(self.canvas.set_allow_overlap)
        self.canvas.set_allow_overlap(self.overlap_button.isChecked())
        tools.addWidget(self.overlap_button)
        side.addLayout(tools)
        side.addWidget(QLabel("Object vs background (raw values):"))
        self.stats_label = QLabel("-")
        self.stats_label.setWordWrap(True)
        side.addWidget(self.stats_label)
        side.addStretch(1)

    @staticmethod
    def _remembered_overlap() -> bool:
        """Last session's Allow Overlap; off when never set."""
        value = QSettings(*_SETTINGS).value(_OVERLAP_KEY)
        if isinstance(value, bool):
            return value
        # QSettings hands back a string on some backends.
        return str(value).strip().lower() in ("true", "1")

    def allow_overlap(self) -> bool:
        return self.overlap_button.isChecked()

    def save_settings(self):
        """Remember Allow Overlap for next time. A panel gets no close
        event of its own, so the window holding it calls this."""
        try:
            QSettings(*_SETTINGS).setValue(_OVERLAP_KEY,
                                           self.allow_overlap())
        except Exception as exc:                  # noqa: BLE001
            debug(f"mask editor settings not saved: "
                  f"{type(exc).__name__}: {exc}")

    # -- data in ------------------------------------------------------------

    def set_labels(self, entries: list):
        """Same entry dicts as the other snippet views (masks included)."""
        self.strip.set_labels(entries)

    # -- snippet cycling ----------------------------------------------------

    def keyPressEvent(self, event):
        # Space / Ctrl+Space step through the strip - the same convention
        # as the canvas's cycle modes, so the habit transfers.
        if event.key() == Qt.Key_Space:
            self.strip.cycle(
                -1 if event.modifiers() & Qt.ControlModifier else 1)
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Steal Space from the mask list so stepping works from there
        too (a list widget would take it as a click)."""
        if (event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Space):
            self.strip.cycle(
                -1 if event.modifiers() & Qt.ControlModifier else 1)
            return True
        return super().eventFilter(obj, event)

    # -- showing a snippet ------------------------------------------------

    def _show_entry(self, entry: "dict | None"):
        self._current = entry
        self._layers = {}
        self._order = []
        self._raw = None
        self._raw_nodata = None
        self._stored_by_name = {}
        self._unreadable = False
        self._undecodable = set()
        size = self.size_spin.value()
        self._display_source = None
        if entry is None:
            self.canvas.set_snippet(None, size, size)
            self.canvas.set_layers({}, [], None)
            self._refresh_mask_list()
            self._refresh_stats()
            self.snippet_shown.emit(None)
            return
        raw = read_label_window_raw(entry["image_path"], entry["pixel_x"],
                                    entry["pixel_y"], size)
        if raw is not None:
            self._raw, self._frame, self._raw_nodata = raw
        else:
            # The file would not open. Anchoring the window at (0, 0) - the
            # old fallback - showed the masks in the wrong place and, worse,
            # committed edits into the image's top-left corner. Take the
            # frame from the dimensions the project already recorded, and
            # in either case let nothing be edited: a user looking at a
            # grey canvas and an empty-looking mask has no way to tell a
            # missing image from an empty one, and Delete is one click away.
            self._unreadable = True
            dims = self._dims_for(entry)
            if dims:
                self._frame = snippet_frame(entry["pixel_x"],
                                            entry["pixel_y"], size,
                                            dims[0], dims[1])
            else:
                self._frame = (0, 0, size, size)
        x0, y0, w, h = self._frame
        # Stored masks re-anchor into the current crop (paint-time snippet
        # size may differ from today's).
        for stored in entry.get("masks") or []:
            name = stored["name"]
            try:
                self._layers[name] = entry_in_window(stored, x0, y0, w, h)
            except (ValueError, KeyError, TypeError) as exc:
                # A mask whose run-length encoding will not decode: the
                # project deliberately keeps it rather than dropping it on
                # load, so the editor must too. Raising here left the
                # editor half-populated - showing the PREVIOUS snippet -
                # and the next stroke committed a mask list missing this
                # entry and everything after it, deleting them for good.
                debug(f"mask {name!r} will not decode: "
                      f"{type(exc).__name__}: {exc}")
                self._undecodable.add(name)
                self._stored_by_name[name] = stored
                self._order.append(name)
                continue
            self._order.append(name)
            self._stored_by_name[name] = stored
        self._display_source = read_label_snippet(
            entry["image_path"], entry["pixel_x"], entry["pixel_y"], size)
        self.canvas.set_snippet(self._adjusted_display(), w, h)
        active = self._order[0] if self._order else None
        self.canvas.set_layers(self._layers, self._order, active)
        self._set_editable(not self._unreadable)
        self._refresh_mask_list(select=active)
        self._refresh_stats()
        self.snippet_shown.emit(entry)

    def _adjusted_display(self) -> "np.ndarray | None":
        """The current snippet's pixels with the view settings applied."""
        if self._display_source is None:
            return None
        return apply_display_adjust(self._display_source,
                                    self.brightness_slider.value(),
                                    self.contrast_slider.value())

    def _apply_display_to_canvas(self):
        """Redraw at the current brightness/contrast, without re-reading."""
        adjusted = self._adjusted_display()
        if adjusted is None:
            return
        h, w = adjusted.shape[:2]
        self.canvas.set_snippet(adjusted, w, h)

    def reset_display_adjust(self):
        """Back to the imagery as it is."""
        for slider in (self.brightness_slider, self.contrast_slider):
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
        self._apply_display_to_canvas()

    def canvas_display_pixels(self) -> "np.ndarray | None":
        """What the canvas is currently showing (for tests)."""
        return self._adjusted_display()

    def _set_editable(self, editable: bool):
        """Paint, Add, Delete and Fill all follow the snippet's readability."""
        self.canvas.set_read_only(not editable)
        for button in (self.add_button, self.delete_button, self.fill_button):
            button.setEnabled(editable)
        tip = ("" if editable else
               "The image could not be read, so its masks are read-only.")
        for button in (self.add_button, self.delete_button):
            button.setToolTip(tip)

    def _on_size_changed(self):
        # Strokes commit as they happen, so the entry dicts already hold the
        # latest masks; re-showing re-anchors them into the new window.
        self._show_entry(self._current)

    # -- mask management ----------------------------------------------------

    def _active_name(self) -> "str | None":
        item = self.mask_list.currentItem()
        return item.text() if item is not None else None

    def _refresh_mask_list(self, select: "str | None" = None):
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for i, name in enumerate(self._order):
            # The row text stays the mask name - every lookup here is by
            # item.text() - so an unreadable mask is marked by style.
            item = QListWidgetItem(name)
            if name in getattr(self, "_undecodable", ()):
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
                item.setForeground(QColor(150, 150, 150))
                item.setToolTip("This mask's encoding could not be read. It "
                                "is kept exactly as stored and cannot be "
                                "edited.")
            color = MASK_COLORS[i % len(MASK_COLORS)]
            pix = QPixmap(12, 12)
            pix.fill(color)
            item.setIcon(QIcon(pix))
            self.mask_list.addItem(item)
            if name == select:
                self.mask_list.setCurrentItem(item)
        if select is None and self.mask_list.count():
            self.mask_list.setCurrentRow(0)
        self.mask_list.blockSignals(False)
        self.canvas.set_active(self._active_name())

    def _on_mask_picked(self, item, _previous=None):
        self.canvas.set_active(item.text() if item is not None else None)
        self._refresh_stats()

    # -- mask-name presets --------------------------------------------------

    def set_mask_names(self, names: list):
        """The project's preset mask names, for the active-name picker."""
        self._mask_names = [str(n) for n in names]
        self._reload_mask_name_combo()

    def mask_names(self) -> list:
        """Every preset held, whatever this snippet already carries."""
        return list(self._mask_names)

    def active_mask_name(self) -> str:
        """The name Add Mask will use: picked, or typed into the combo."""
        return self.mask_name_combo.currentText().strip()

    def set_active_mask_name(self, name: str):
        """Make ``name`` the one Add Mask uses (and paint it if present)."""
        self.mask_name_combo.setCurrentText(name)
        self._on_active_mask_name_picked()

    def _reload_mask_name_combo(self):
        """Refill the picker, keeping whatever name was active.

        Every preset stays listed however many masks are painted: the
        list is what this project calls things, not a tray of names to
        be used up.
        """
        keep = self.mask_name_combo.currentText()
        self.mask_name_combo.blockSignals(True)
        self.mask_name_combo.clear()
        self.mask_name_combo.addItems(self._mask_names)
        self.mask_name_combo.setCurrentText(keep or (self._mask_names[0]
                                                     if self._mask_names
                                                     else ""))
        self.mask_name_combo.blockSignals(False)

    def _on_active_mask_name_picked(self, *_args):
        """Picking a name the snippet already carries selects that mask.

        Choosing "hull" when there is a hull means paint the hull - the
        same move as switching the active class and carrying on.
        """
        name = self.active_mask_name()
        if name and name in self._layers and name != self._active_name():
            self.canvas.set_active(name)
            self._refresh_mask_list(select=name)
            self._refresh_stats()

    def _remember_mask_name(self, name: str):
        """Keep a typed name, so the next snippet can be given it.

        The name used on this snippet is nearly always the name wanted on
        the next one; making the user retype it is what produced the
        near-miss names in the first place.
        """
        name = name.strip()
        if not name or name in self._mask_names:
            return
        self._mask_names.append(name)
        self._reload_mask_name_combo()
        self.mask_names_changed.emit(list(self._mask_names))

    def _on_add_mask(self):
        """Add a mask with the active name and hand the brush to it.

        No prompt. The name is already chosen - in the picker above the
        button - so this is one click between deciding to mask something
        and painting it, on every snippet in the strip.
        """
        if self._current is None:
            return
        name = self.active_mask_name()
        if not name:
            self._set_name_note("Type or pick a mask name first.")
            return
        self._set_name_note("")
        if name in self._layers:
            # Already here: select it rather than refusing. The button
            # means "paint this mask", and an error box in the middle of
            # a run of snippets is worse than doing the obvious thing.
            self.canvas.set_active(name)
            self._refresh_mask_list(select=name)
            self._refresh_stats()
            return
        _x0, _y0, w, h = self._frame
        self._layers[name] = np.zeros((h, w), dtype=bool)
        self._order.append(name)
        self._remember_mask_name(name)
        self.canvas.set_layers(self._layers, self._order, name)
        self._refresh_mask_list(select=name)
        self._emit_masks()

    def _set_name_note(self, text: str):
        self.name_note.setText(text)
        self.name_note.setVisible(bool(text))

    def _on_delete_mask(self):
        name = self._active_name()
        if name is None or self._current is None:
            return
        self._layers.pop(name, None)
        self._stored_by_name.pop(name, None)
        self._undecodable.discard(name)
        if name in self._order:
            self._order.remove(name)
        nxt = self._order[0] if self._order else None
        self.canvas.set_layers(self._layers, self._order, nxt)
        self._refresh_mask_list(select=nxt)
        self._emit_masks()
        self._refresh_stats()

    # -- persistence + stats ------------------------------------------------

    def _on_fill_enclosed(self):
        """Fill the active mask's enclosed interior (a drawn hull)."""
        name = self._active_name()
        if name is None or name not in self._layers:
            return
        barrier = None
        if not self.allow_overlap():
            # The other masks are walls: the shadow drawn up to the hull
            # is closed by the hull's edge, and the fill stops there.
            others = [layer for other, layer in self._layers.items()
                      if other != name and layer is not None]
            if others:
                barrier = np.zeros_like(self._layers[name])
                for layer in others:
                    barrier |= layer
        filled, added = fill_enclosed(self._layers[name], barrier=barrier)
        if added == 0:
            QMessageBox.information(
                self, "Nothing enclosed",
                f"'{name}' does not fully enclose any area - the outline "
                "probably has a gap. Close the hull and try again.")
            return
        self._layers[name][:] = filled
        self.canvas.invalidate_layer(name)
        self._emit_masks()
        self._refresh_stats()

    def _on_stroke_finished(self):
        self._emit_masks()
        self._refresh_stats()

    def _dims_for(self, entry: dict) -> "tuple | None":
        """The image's (width, height): from the project when it knows them,
        else header-read once and cached. None only when the file itself is
        unreadable - the caller then falls back to windowed anchoring.
        """
        size = entry.get("image_size")
        if size:
            return size
        path = entry["image_path"]
        if path not in self._image_dims:
            try:
                import rasterio
                with rasterio.open(path) as src:
                    self._image_dims[path] = (src.width, src.height)
            except Exception:
                self._image_dims[path] = None
        return self._image_dims[path]

    def _emit_masks(self):
        if self._current is None:
            return
        x0, y0, _w, _h = self._frame
        image_size = self._dims_for(self._current)
        undecodable = getattr(self, "_undecodable", ())
        entries = [self._stored_by_name[name] if name in undecodable
                   else merged_entry(name, x0, y0, self._layers[name],
                                     self._stored_by_name.get(name),
                                     image_size=image_size)
                   for name in self._order]
        self._stored_by_name = {e["name"]: e for e in entries}
        self._current["masks"] = entries
        self.strip.refresh_current()
        self.masks_changed.emit(self._current["label_id"], entries)

    def _refresh_stats(self):
        if getattr(self, "_unreadable", False):
            self.stats_label.setText(
                "Image unreadable - masks shown read-only.\n"
                f"{self._current['image_path'] if self._current else ''}")
            return
        name = self._active_name()
        if (name is None or self._raw is None
                or name not in self._layers):
            self.stats_label.setText("-")
            return
        stats = mask_statistics(self._raw, self._layers[name],
                                nodata=self._raw_nodata)
        if stats is None:
            self.stats_label.setText(
                "Paint some pixels to compare the object's raw values "
                "with the background.")
            return
        lines = [f"'{name}': {stats['pixels_object']} px object, "
                 f"{stats['pixels_background']} px background"]
        for band, (om, os_, bm, bs) in enumerate(zip(
                stats["object_mean"], stats["object_std"],
                stats["background_mean"], stats["background_std"]), 1):
            lines.append(f"B{band}: obj {om:.1f}\N{PLUS-MINUS SIGN}{os_:.1f}"
                         f"  bg {bm:.1f}\N{PLUS-MINUS SIGN}{bs:.1f}")
        self.stats_label.setText("\n".join(lines))


class MaskSection(Section):
    """The Snippet Editor's Masks section (see section.py).

    Its panel is the mask editor's own - list, name, add, delete, fill,
    overlap, statistics - and it owns the Paint tool. Switched off, the
    canvas shows no layers and takes no strokes. In the Grid view it waits:
    masks are painted on the snippet in hand, which the grid does not show.
    """

    key = "masks"
    title = "Masks"
    tool = (TOOL_PAINT, "Paint masks",
            "Left-drag paints the active mask, right-drag erases.")

    def __init__(self, editor: MaskEditor, parent=None):
        super().__init__(MASK_WORKLIST, editor.mask_panel, parent)
        self._editor = editor
        editor.masks_changed.connect(
            lambda label_id, _masks: self.entry_changed.emit(label_id))

    def set_shown(self, shown):
        super().set_shown(shown)
        self._editor.canvas.set_layers_shown(shown)

    def view_changed(self, single):
        self.panel.setEnabled(single)
        return ("" if single else "Masks are painted in the Single view - "
                                  "double-click a snippet to open it there.")
