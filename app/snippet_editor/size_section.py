"""The size section: a label's length and width, measured on its snippet.

Putting a size on a label used to be done on the map canvas (right-click
a label, or M). It is done here now, and only here; the canvas keeps its
Ruler, which measures a distance without touching any label.

Why here: a snippet is raw source pixels, zoomable to 32x, so a line can
be placed far more precisely than on the reprojected map - and it is
always the label's OWN image, which the canvas had to work out (in the
waterfall a measurement once came out 127 m or 510 m depending on which
stacked image was asked). The metres are the same metres: both ends of
the line go through the image's georeferencing to WGS84 and are measured
on the ellipsoid (orientation_math.ground_length_m), as the map did.

With the Measure tool, the first line drawn is the length and the second
the width. Each is stored as it is drawn; a further line corrects the
WIDTH, never quietly the length, and either can be chosen to redraw (the
two radio buttons, or the L and W keys). Right-click, or Clear, takes
both away.

"Apply size to linked labels" gives the same size to every label of the
same object - the labeller measures its best view - or, switched off,
each view is measured on its own. It replaced Options > Wire meas. to
linked objects, and is remembered per user, not per project.

The length line is deliberately NOT an orientation: a label may be
asymmetric, and nothing guarantees its heading runs along its length.

Only the numbers are stored. The lines themselves are remembered for the
session, so coming back to a snippet shows what was measured and where.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QLabel, QPushButton,
                             QRadioButton, QVBoxLayout, QWidget)

from ..settings_scope import settings
from ..orientation_math import ground_length_m
from .section import Section
from .single_view import TOOL_MEASURE
from .strip import Worklist

LENGTH, WIDTH = "length", "width"
_FIELD = {LENGTH: "length_m", WIDTH: "width_m"}
_LINKED_KEY = "snippet_editor/size_linked"
_NO_METRES = ("This image has no georeferencing, so there are no metres "
              "to measure.")


def size_text(length_m, width_m) -> str:
    """'12.3x4.1 m', with ? for the half not measured; '' for neither."""
    if length_m is None and width_m is None:
        return ""
    length = f"{length_m:.1f}" if length_m is not None else "?"
    width = f"{width_m:.1f}" if width_m is not None else "?"
    return f"{length}\N{MULTIPLICATION SIGN}{width} m"


def _badge(entry: dict) -> str:
    text = size_text(entry.get("length_m"), entry.get("width_m"))
    return f"  [{text}]" if text else ""


# Done once BOTH are measured: a label with only its length still needs
# its width, and belongs on the list of what is left.
SIZE_WORKLIST = Worklist(
    needs="Needs size", done="Sized",
    is_done=lambda entry: (entry.get("length_m") is not None
                           and entry.get("width_m") is not None),
    badge=_badge,
    nothing_left="Every snippet here has a length and a width. "
                 "Nothing left to do.",
    none_done="No snippet here has been measured yet.")


def shares_with_linked() -> bool:
    """Whether a size is shared with the labels of the same object.

    Off unless somebody asked for it, as the Options toggle it replaced
    was. The main window asks too, when two labels are linked.
    """
    value = settings().value(_LINKED_KEY)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1")


class SizePanel(QWidget):
    """What the snippet in hand measures, and which line comes next."""

    clear_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel("With the Measure tool, draw the length, then the "
                       "width. The next line sets:")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._group = QButtonGroup(self)
        self.buttons = {}
        for target, key in ((LENGTH, "L"), (WIDTH, "W")):
            button = QRadioButton()
            button.setToolTip(f"The next line drawn sets the {target} "
                              f"(key {key}).")
            self._group.addButton(button)
            self.buttons[target] = button
            layout.addWidget(button)
        self.buttons[LENGTH].setChecked(True)
        self.live_label = QLabel("")
        layout.addWidget(self.live_label)
        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: #b26a00;")
        self.note_label.hide()
        layout.addWidget(self.note_label)

        self.linked_check = QCheckBox("Apply size to linked labels")
        self.linked_check.setToolTip(
            "On: every label of the same object gets this size - measure\n"
            "its best view once. Off: each view is measured on its own.")
        self.linked_check.setChecked(shares_with_linked())
        self.linked_check.toggled.connect(
            lambda on: settings().setValue(_LINKED_KEY, bool(on)))
        layout.addWidget(self.linked_check)

        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip(
            "Take the length and width off this label (or right-click the "
            "snippet with the Measure tool).")
        self.clear_button.clicked.connect(self.clear_requested)
        layout.addWidget(self.clear_button)
        hint = QLabel("Metres on the ground, measured on the ellipsoid. "
                      "Right-click clears; L and W choose the line.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
        self.show_size(None, None, available=False)

    def target(self) -> str:
        return LENGTH if self.buttons[LENGTH].isChecked() else WIDTH

    def set_target(self, target: str):
        self.buttons[target].setChecked(True)

    def show_size(self, length_m, width_m, available: bool, note: str = ""):
        for target, value in ((LENGTH, length_m), (WIDTH, width_m)):
            shown = f"{value:.1f} m" if value is not None else "not measured"
            self.buttons[target].setText(f"{target.capitalize()}: {shown}")
            self.buttons[target].setEnabled(available)
        self.clear_button.setEnabled(
            available and (length_m is not None or width_m is not None))
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))
        self.live_label.setText("")

    def show_live(self, target: str, metres):
        self.live_label.setText(
            "" if metres is None else f"{target.capitalize()}: "
                                      f"{metres:.2f} m\N{HORIZONTAL ELLIPSIS}")

    def readout_text(self) -> str:
        """Everything the panel says about this snippet (for tests)."""
        return "  ".join(text for text in (
            self.buttons[LENGTH].text(), self.buttons[WIDTH].text(),
            self.note_label.text()) if text)


class SizeSection(Section):
    """The Snippet Editor's Size section; it owns the Measure tool."""

    key = "size"
    title = "Size"
    tool = (TOOL_MEASURE, "Measure",
            "Left-drag draws the length, then the width, in metres on the\n"
            "ground. Right-click clears them.")

    # (label_id, length_m or None, width_m or None) - to the main window.
    size_changed = pyqtSignal(int, object, object)

    def __init__(self, single, geo_info, parent=None):
        """``single`` is the Single view (its canvas, its crop, its
        strip); ``geo_info(image_path)`` gives (affine, crs, w, h) from
        the file, as the orientation grid reads it."""
        super().__init__(SIZE_WORKLIST, SizePanel(), parent)
        self._single = single
        self._geo_info = geo_info
        self._entry = None
        self._geo = (None, None)
        # label_id -> {LENGTH/WIDTH: (col_s, row_s, col_e, row_e)} in
        # SOURCE pixels: this session's lines, to draw on coming back.
        self._lines: dict = {}
        canvas = single.canvas
        canvas.measure_drawn.connect(self._on_drawn)
        canvas.measure_moved.connect(self._on_moved)
        canvas.measure_clear_requested.connect(self.clear)
        self.panel.clear_requested.connect(self.clear)

    # -- the snippet in hand -------------------------------------------------

    def show_entry(self, entry):
        self._entry = entry
        self._geo = (None, None)
        if entry is not None:
            affine, crs, _w, _h = self._geo_info(entry["image_path"])
            self._geo = (affine, crs)
        if entry is not None:
            # Pick up where this label was left: the width, once it has a
            # length.
            self.panel.set_target(
                WIDTH if entry.get("length_m") is not None else LENGTH)
        self._redisplay()

    def refresh(self, label_id):
        if self._entry is not None and self._entry["label_id"] == label_id:
            self._redisplay()

    def _available(self) -> bool:
        return self._entry is not None and None not in self._geo

    def _redisplay(self):
        entry = self._entry
        note = _NO_METRES if entry is not None and not self._available() \
            else ""
        self.panel.show_size(
            None if entry is None else entry.get("length_m"),
            None if entry is None else entry.get("width_m"),
            available=self._available(), note=note)
        self._show_lines()

    def _show_lines(self):
        canvas = self._single.canvas
        if not self.shown or self._entry is None:
            canvas.set_measure_lines([])
            return
        x0, y0, _w, _h = self._single._frame
        drawn = []
        for target, line in self._lines.get(
                self._entry["label_id"], {}).items():
            value = self._entry.get(_FIELD[target])
            if value is None:
                continue
            cs, rs, ce, re_ = line
            drawn.append((cs - x0, rs - y0, ce - x0, re_ - y0,
                          f"{target[0].upper()} {value:.1f} m"))
        canvas.set_measure_lines(drawn)

    # -- measuring -------------------------------------------------------------

    def _source_line(self, sx, sy, ex, ey):
        """Snippet pixels -> source pixels: near an edge the crop is
        clamped, so the line must be measured where it really is."""
        x0, y0, _w, _h = self._single._frame
        return (x0 + sx, y0 + sy, x0 + ex, y0 + ey)

    def _on_moved(self, sx, sy, ex, ey):
        if not (self.shown and self._available()):
            return
        self.panel.show_live(self.panel.target(), ground_length_m(
            *self._source_line(sx, sy, ex, ey), *self._geo))

    def _on_drawn(self, sx, sy, ex, ey):
        if not (self.shown and self._available()):
            return
        line = self._source_line(sx, sy, ex, ey)
        metres = ground_length_m(*line, *self._geo)
        if metres is None:
            return
        target = self.panel.target()
        entry = self._entry
        entry[_FIELD[target]] = metres
        self._lines.setdefault(entry["label_id"], {})[target] = line
        # On to the width - and there it stays: a stray further drag
        # corrects the width rather than quietly replacing the length.
        self.panel.set_target(WIDTH)
        self._commit(entry)

    def clear(self):
        """Take the size off the snippet in hand."""
        if not self._available():
            return
        entry = self._entry
        if entry.get("length_m") is None and entry.get("width_m") is None:
            return
        entry["length_m"] = entry["width_m"] = None
        self._lines.pop(entry["label_id"], None)
        self.panel.set_target(LENGTH)
        self._commit(entry)

    def _commit(self, entry):
        """Send the size out - to the labels of the same object as well,
        when that is switched on."""
        length_m, width_m = entry.get("length_m"), entry.get("width_m")
        changed = [entry]
        if self.panel.linked_check.isChecked() and entry.get("linked"):
            for other in self._single.strip.entries:
                if (other is not entry
                        and other.get("object_id") == entry.get("object_id")):
                    other["length_m"], other["width_m"] = length_m, width_m
                    changed.append(other)
        self._redisplay()
        for each in changed:
            self.size_changed.emit(each["label_id"], length_m, width_m)
            self.entry_changed.emit(each["label_id"])

    # -- the section interface -------------------------------------------------

    def set_shown(self, shown):
        super().set_shown(shown)
        self._show_lines()

    def view_changed(self, single):
        self.panel.setEnabled(single)
        return ("" if single else "Sizes are measured in the Single view - "
                                  "double-click a snippet to open it there.")

    def key_pressed(self, key):
        target = {Qt.Key_L: LENGTH, Qt.Key_W: WIDTH}.get(key)
        if target is None or not self._available():
            return False
        self.panel.set_target(target)
        return True
