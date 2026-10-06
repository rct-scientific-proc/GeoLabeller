"""The Model Review window: the model's mistakes in a grid, labelled.

Opened from Labels > Model Review... It reads a file of chips (source.py),
shows the ones the model called one class from most to least sure, top
left to bottom right, and takes verdicts: a click gives a chip the
active label - a class from the New label box, or Ignore - Shift-click a
run of them, Ctrl-click picks several for a key to label at once. Import
to project hands the verdicts to the main window, which makes the labels
and the ledger entries in one undo step; the chips then leave the grid,
and stay off it in later rounds (ledger.py), unless Show reviewed.

The chip size is the user's, as in the Snippet Editor: the file says
only where the centre is. Columns set how many chips share the width.

Nothing here touches the project: the window reads it (through the
getter it is given, so it is always the current one) and reports what
the user decided through signals.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout,
                             QHBoxLayout, QInputDialog, QLabel,
                             QMessageBox, QPushButton, QScrollArea,
                             QSpinBox, QVBoxLayout, QWidget)

from .. import identity, recent
from ..settings_scope import settings as app_settings
from ..snippets import SnippetLoader
from .ledger import DEFAULT_TOLERANCE_PX, IGNORE, find_verdict
from .source import by_class, match_candidates, read_candidates

CAPTION_PX = 20                 # the strip under each chip's picture
GRID_SPACING = 8
MIN_CELL_PX = 64
DEFAULT_CHIP_PX = 224           # source pixels read around the centre
DEFAULT_COLUMNS = 6
ALL_CLASSES = None              # the predicted-class box's first entry

SELECTED_COLOR = QColor(255, 255, 255)
IGNORE_COLOR = QColor(150, 150, 150)
EARLIER_SHADE = QColor(0, 0, 0, 150)
TEXT_COLOR = QColor(220, 220, 220)

_CHIP_KEY = "model_review/chip_size"
_COLUMNS_KEY = "model_review/columns"
_SHOW_REVIEWED_KEY = "model_review/show_reviewed"
_TOLERANCE_KEY = "model_review/tolerance"
_GEOMETRY_KEY = "model_review/geometry"


class ChipCell(QWidget):
    """One chip: its picture, its score, and what it has become."""

    clicked = pyqtSignal(int, object)       # key, keyboard modifiers
    cleared = pyqtSignal(int)               # right-click: take it back

    def __init__(self, key: int, parent=None):
        super().__init__(parent)
        self.key = key
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._score = ""
        self._verdict = ""
        self._colour: QColor | None = None
        self._selected = False
        self._earlier = False
        self._pressed = None
        self.setCursor(Qt.PointingHandCursor)
        self.set_size(128)

    def set_size(self, px: int):
        self._px = int(px)
        self._scaled = None
        self.setFixedSize(self._px, self._px + CAPTION_PX)
        self.update()

    def set_pixels(self, arr: np.ndarray):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self._scaled = None
        self.update()

    def set_state(self, score: str, verdict: str, colour: QColor | None,
                  selected: bool, earlier: bool):
        self._score, self._verdict, self._colour = score, verdict, colour
        self._selected, self._earlier = selected, earlier
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        picture = QRect(0, 0, self._px, self._px)
        if self._pixmap is not None:
            if self._scaled is None or self._scaled.width() != self._px:
                self._scaled = self._pixmap.scaled(
                    self._px, self._px, Qt.KeepAspectRatio,
                    Qt.SmoothTransformation)
            x = (self._px - self._scaled.width()) // 2
            y = (self._px - self._scaled.height()) // 2
            painter.drawPixmap(x, y, self._scaled)
        if self._earlier:
            painter.fillRect(picture, EARLIER_SHADE)
        border = SELECTED_COLOR if self._selected else self._colour
        if border is not None:
            painter.setPen(QPen(border, 3))
            painter.drawRect(picture.adjusted(1, 1, -2, -2))
        caption = QRect(0, self._px, self._px, CAPTION_PX)
        painter.setPen(TEXT_COLOR)
        painter.drawText(caption.adjusted(4, 0, -4, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, self._score)
        if self._verdict:
            painter.setPen(self._colour or TEXT_COLOR)
            painter.drawText(caption.adjusted(4, 0, -4, 0),
                             Qt.AlignRight | Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(
                                 self._verdict, Qt.ElideRight,
                                 self._px - 48))
        painter.end()

    def mousePressEvent(self, event):
        self._pressed = (event.button(), event.modifiers())

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, None
        if pressed is None or not self.rect().contains(event.pos()):
            return
        button, modifiers = pressed
        if button == Qt.LeftButton:
            self.clicked.emit(self.key, modifiers)
        elif button == Qt.RightButton:
            self.cleared.emit(self.key)


class ModelReviewWindow(QWidget):
    """The window. ``project_getter()`` returns the current project."""

    # (chips with a verdict, the file's name): make them labels, please.
    import_requested = pyqtSignal(list, str)
    class_requested = pyqtSignal(str)       # add this class to the project
    save_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()

    def __init__(self, project_getter, parent=None):
        super().__init__(parent, Qt.Window)
        self._project = project_getter
        self.setWindowTitle("Model Review")
        self.setFocusPolicy(Qt.StrongFocus)
        self._chips: list = []
        self._by_key: dict = {}
        self._visible: list = []
        self._source = ""
        self._report = None
        self._classes: list = []
        self._colours: list = []
        self._selected: set = set()
        self._anchor: int | None = None
        self._active: str | None = None
        self._cells: dict = {}
        self.loader = SnippetLoader(self)
        self.loader.ready.connect(self._on_pixels)
        self._setup_ui()
        self._restore_settings()

    # -- layout ---------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.open_button = QPushButton("Open file...")
        self.open_button.setToolTip(
            "A JSON list of the model's mistakes: image, image_width,\n"
            "image_height, x, y, class, score. Ctrl+O.")
        self.open_button.clicked.connect(self.open_dialog)
        top.addWidget(self.open_button)
        self.file_label = QLabel("No file open")
        top.addWidget(self.file_label, 1)
        top.addWidget(QLabel("Predicted:"))
        self.class_filter = QComboBox()
        self.class_filter.setToolTip(
            "Which of the model's calls to look at. Each list runs from\n"
            "the chip it was surest of to the one it was least.")
        self.class_filter.setMinimumWidth(160)
        self.class_filter.currentIndexChanged.connect(self._on_filter)
        top.addWidget(self.class_filter)
        top.addSpacing(12)
        top.addWidget(QLabel("Chip size"))
        self.chip_spin = QSpinBox()
        self.chip_spin.setRange(32, 2048)
        self.chip_spin.setSingleStep(32)
        self.chip_spin.setValue(DEFAULT_CHIP_PX)
        self.chip_spin.setSuffix(" px")
        self.chip_spin.setToolTip(
            "Source pixels read around each chip's centre - the file says\n"
            "only where the centre is.")
        self.chip_spin.setKeyboardTracking(False)
        self.chip_spin.valueChanged.connect(self._on_chip_size)
        top.addWidget(self.chip_spin)
        top.addWidget(QLabel("Columns"))
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 16)
        self.columns_spin.setValue(DEFAULT_COLUMNS)
        self.columns_spin.setKeyboardTracking(False)
        self.columns_spin.valueChanged.connect(lambda _v: self._rebuild())
        top.addWidget(self.columns_spin)
        layout.addLayout(top)

        second = QHBoxLayout()
        second.addWidget(QLabel("New label:"))
        self.label_combo = QComboBox()
        self.label_combo.setMinimumWidth(160)
        self.label_combo.setToolTip(
            "The class a click gives a chip. Keys 1-9 pick by place in\n"
            "the list, and label whatever is selected.")
        self.label_combo.activated.connect(self._on_label_picked)
        second.addWidget(self.label_combo)
        self.add_class_button = QPushButton("Add class...")
        self.add_class_button.setToolTip(
            "A class the project does not have yet - rock, wake, whatever\n"
            "the clutter is - added to the project and made active.")
        self.add_class_button.clicked.connect(self._add_class)
        second.addWidget(self.add_class_button)
        self.ignore_button = QPushButton("Ignore (0)")
        self.ignore_button.setCheckable(True)
        self.ignore_button.setToolTip(
            "Looked at, and nothing to label: the chip is remembered as\n"
            "reviewed and makes no label.")
        self.ignore_button.clicked.connect(self._on_ignore_clicked)
        second.addWidget(self.ignore_button)
        second.addSpacing(12)
        self.show_reviewed_check = QCheckBox("Show reviewed")
        self.show_reviewed_check.setToolTip(
            "Chips reviewed in an earlier round, shaded, with what they\n"
            "became. Kept off the grid otherwise.")
        self.show_reviewed_check.toggled.connect(lambda _on: self._rebuild())
        second.addWidget(self.show_reviewed_check)
        second.addWidget(QLabel("Same chip within"))
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(1, 500)
        self.tolerance_spin.setValue(int(DEFAULT_TOLERANCE_PX))
        self.tolerance_spin.setSuffix(" px")
        self.tolerance_spin.setToolTip(
            "A chip whose centre lies this close to one reviewed earlier,\n"
            "on the same image, is that chip again.")
        self.tolerance_spin.setKeyboardTracking(False)
        self.tolerance_spin.valueChanged.connect(
            lambda _v: self.refresh_earlier())
        second.addWidget(self.tolerance_spin)
        second.addStretch(1)
        self.import_button = QPushButton("Import to project")
        self.import_button.setToolTip(
            "Every labelled chip becomes a label on its image, with what\n"
            "the model said in its description; ignored chips make none.\n"
            "One undo step. The chips then leave the grid.")
        self.import_button.clicked.connect(self._import)
        second.addWidget(self.import_button)
        layout.addLayout(second)

        self.hint = QLabel(
            "Click a chip to give it the label above; Shift-click labels "
            "the run from the last click. Ctrl-click picks chips, then a "
            "number key labels the picked. 0 is Ignore; right-click or "
            "Delete takes a verdict back.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self._grid_host)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._show_counts()

    # -- data in --------------------------------------------------------------

    def set_classes(self, names, colours=None):
        """The project's classes, in order, with their marker colours."""
        self._classes = list(names)
        self._colours = list(colours or [])
        combo = self.label_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self._classes)
        combo.blockSignals(False)
        if self._active not in self._classes and self._active != IGNORE:
            self._active = self._classes[0] if self._classes else None
        self._show_active()
        self._refresh_cells()

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the model's mistakes",
            recent.last_dir(recent.REVIEW), "JSON (*.json);;All Files (*)")
        if path:
            recent.remember_dir(recent.REVIEW, path)
            self.open_file(path)

    def open_file(self, path) -> bool:
        """Read and match a file of chips. False, having said why, when
        nothing in it could be shown."""
        name = Path(str(path)).name
        try:
            candidates, malformed = read_candidates(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._say(f"Could not read {name} as a file of chips.\n\n"
                      f"{type(exc).__name__}: {exc}")
            return False
        chips, report = match_candidates(candidates, self._project(),
                                         malformed)
        if not chips:
            self._say(f"Nothing in {name} could be matched to this "
                      f"project.\n\n{report.summary()}.")
            return False
        self._source, self._chips, self._report = name, chips, report
        self._by_key = {chip.key: chip for chip in chips}
        self._selected.clear()
        self._anchor = None
        self.setWindowTitle(f"Model Review - {name}")
        self.file_label.setText(f"{name}: {report.summary()}")
        self.refresh_earlier()
        self._fill_filter()
        self._rebuild()
        return True

    def refresh_earlier(self):
        """Ask the project's memory which chips were reviewed before."""
        ledger = getattr(self._project(), "model_review", [])
        tolerance = float(self.tolerance_spin.value())
        for chip in self._chips:
            chip.earlier = find_verdict(ledger, chip.image_name, chip.pixel_x,
                                        chip.pixel_y, tolerance)
            if chip.earlier is not None:
                chip.verdict = None
        self._fill_filter()
        self._rebuild()

    # -- what is shown --------------------------------------------------------

    def chips(self) -> list:
        return list(self._chips)

    def source(self) -> str:
        return self._source

    def shown(self) -> list:
        """The chips on the grid, in its order."""
        return list(self._visible)

    def shown_keys(self) -> list:
        return [chip.key for chip in self._visible]

    def selected(self) -> set:
        return set(self._selected)

    def active(self) -> str | None:
        return self._active

    def with_verdicts(self) -> list:
        return [chip for chip in self._chips if chip.verdict is not None]

    def predicted_class(self):
        return self.class_filter.currentData()

    def set_predicted_class(self, name):
        index = self.class_filter.findData(name)
        if index >= 0:
            self.class_filter.setCurrentIndex(index)

    def set_show_reviewed(self, on: bool):
        self.show_reviewed_check.setChecked(bool(on))

    def _fill_filter(self):
        combo = self.class_filter
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        show_reviewed = self.show_reviewed_check.isChecked()
        pool = [chip for chip in self._chips
                if show_reviewed or not chip.reviewed_before()]
        grouped = by_class(pool)
        combo.addItem(f"All classes ({len(pool)})", ALL_CLASSES)
        for name in sorted(grouped):
            combo.addItem(f"{name} ({len(grouped[name])})", name)
        index = combo.findData(current) if current is not None else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _visible_chips(self) -> list:
        wanted = self.class_filter.currentData()
        show_reviewed = self.show_reviewed_check.isChecked()
        chosen = [chip for chip in self._chips
                  if (wanted is ALL_CLASSES or chip.predicted == wanted)
                  and (show_reviewed or not chip.reviewed_before())]
        return sorted(chosen, key=lambda c: (-c.score, c.key))

    def _cell_px(self) -> int:
        columns = max(1, self.columns_spin.value())
        width = self.scroll.viewport().width() - GRID_SPACING * (columns + 1)
        return max(MIN_CELL_PX, width // columns)

    def _rebuild(self):
        self.loader.cancel_all()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cells.clear()
        self._visible = self._visible_chips()
        keys = {chip.key for chip in self._visible}
        self._selected &= keys
        if self._anchor not in keys:
            self._anchor = None
        columns = max(1, self.columns_spin.value())
        px = self._cell_px()
        for i, chip in enumerate(self._visible):
            cell = ChipCell(chip.key)
            cell.set_size(px)
            cell.setToolTip(
                f"{chip.image_name}  ({chip.pixel_x:.1f}, {chip.pixel_y:.1f})"
                f"\nmodel: {chip.predicted} {chip.score:.2f}"
                + ("\n(scaled from the size the file stated)"
                   if chip.rescaled else ""))
            cell.clicked.connect(self._on_cell_clicked)
            cell.cleared.connect(self._on_cell_cleared)
            self._grid.addWidget(cell, i // columns, i % columns)
            self._cells[chip.key] = cell
        self._refresh_cells()
        self._request_pixels()
        self._show_counts()

    def _request_pixels(self):
        size = self.chip_spin.value()
        for chip in self._visible:
            self.loader.request(chip.key, chip.image_path, chip.pixel_x,
                                chip.pixel_y, size)

    def _on_pixels(self, key, arr):
        cell = self._cells.get(key)
        if cell is not None and arr is not None:
            cell.set_pixels(arr)

    def _on_chip_size(self, _value):
        self._request_pixels()

    def eventFilter(self, obj, event):
        if obj is self.scroll.viewport() and event.type() == event.Resize:
            px = self._cell_px()
            for cell in self._cells.values():
                if cell._px != px:
                    cell.set_size(px)
        return super().eventFilter(obj, event)

    def _colour_of(self, class_name) -> QColor | None:
        if class_name == IGNORE:
            return IGNORE_COLOR
        if class_name in self._classes and self._colours:
            return self._colours[self._classes.index(class_name)
                                 % len(self._colours)]
        return TEXT_COLOR if class_name else None

    def _refresh_cells(self):
        for chip in self._visible:
            cell = self._cells.get(chip.key)
            if cell is None:
                continue
            if chip.reviewed_before():
                became = chip.earlier.became
                text = "earlier: " + ("Ignore" if became == IGNORE
                                      else became)
                colour = self._colour_of(became)
            else:
                text = ("" if chip.verdict is None
                        else "Ignore" if chip.verdict == IGNORE
                        else chip.verdict)
                colour = (None if chip.verdict is None
                          else self._colour_of(chip.verdict))
            cell.set_state(f"{chip.score:.2f}", text, colour,
                           chip.key in self._selected,
                           chip.reviewed_before())

    def _show_counts(self):
        verdicts = self.with_verdicts()
        labelled = sum(1 for chip in verdicts if chip.verdict != IGNORE)
        ignored = len(verdicts) - labelled
        n = len(verdicts)
        self.import_button.setEnabled(n > 0)
        self.import_button.setText(
            f"Import {n} to project" if n else "Import to project")
        if not self._chips:
            self.status.setText("Open a file of the model's mistakes to "
                                "begin.")
            return
        earlier = sum(1 for chip in self._chips if chip.reviewed_before())
        shown = len(self._visible)
        left = sum(1 for chip in self._visible
                   if chip.verdict is None and not chip.reviewed_before())
        text = (f"{shown} of {len(self._chips)} chips shown: {left} to look "
                f"at, {labelled} labelled, {ignored} to ignore")
        if earlier:
            text += (f"; {earlier} reviewed in an earlier round"
                     + ("" if self.show_reviewed_check.isChecked()
                        else ", hidden"))
        self.status.setText(text)

    def _on_filter(self, _index):
        self._rebuild()

    # -- verdicts -------------------------------------------------------------

    def set_active(self, verdict):
        """The label a click gives: a class name, or IGNORE. Also given to
        whatever is selected now."""
        if verdict != IGNORE and verdict not in self._classes:
            return
        self._active = verdict
        self._show_active()
        self._apply_active(self._selected)

    def _show_active(self):
        combo = self.label_combo
        combo.blockSignals(True)
        if self._active in self._classes:
            combo.setCurrentIndex(self._classes.index(self._active))
        combo.blockSignals(False)
        self.ignore_button.setChecked(self._active == IGNORE)

    def _apply_active(self, keys):
        if self._active is None or not keys:
            return
        for key in keys:
            chip = self._by_key.get(key)
            if chip is not None and not chip.reviewed_before():
                chip.verdict = self._active
        self._refresh_cells()
        self._show_counts()

    def clear_verdicts(self, keys):
        for key in keys:
            chip = self._by_key.get(key)
            if chip is not None:
                chip.verdict = None
        self._refresh_cells()
        self._show_counts()

    def _on_label_picked(self, index):
        self.setFocus()
        if 0 <= index < len(self._classes):
            self.set_active(self._classes[index])

    def _on_ignore_clicked(self, checked):
        self.setFocus()
        if checked:
            self.set_active(IGNORE)
        elif self._classes:
            self.set_active(self._classes[self.label_combo.currentIndex()]
                            if self.label_combo.currentIndex() >= 0
                            else self._classes[0])
        else:
            self.ignore_button.setChecked(True)

    def _add_class(self):
        self.setFocus()
        name = identity.clean(self._ask_class_name())
        if not name:
            return
        if name not in self._classes:
            self.class_requested.emit(name)
        if name in self._classes:
            self.set_active(name)

    def _ask_class_name(self) -> str:
        """The Add class box; a method of its own so tests can answer it."""
        text, accepted = QInputDialog.getText(
            self, "Add class", "Class name for these chips:")
        return text if accepted else ""

    def _on_cell_clicked(self, key, modifiers):
        """A plain click labels the chip and a Shift-click the run from
        the last click, and both leave nothing selected: the next key
        picks the label for what comes next, as on the canvas. Only a
        Ctrl-click builds a selection, for a key to label at once."""
        self.setFocus()
        order = self.shown_keys()
        if key not in order:
            return
        if modifiers & Qt.ControlModifier:
            if key in self._selected:
                self._selected.discard(key)
            else:
                self._selected.add(key)
            self._anchor = key
            self._refresh_cells()
            return
        if modifiers & Qt.ShiftModifier and self._anchor in order:
            start, end = sorted((order.index(self._anchor), order.index(key)))
            run = order[start:end + 1]
        else:
            run = [key]
        self._anchor = key
        self._selected.clear()
        self._apply_active(run)
        self._refresh_cells()

    def _on_cell_cleared(self, key):
        self.setFocus()
        self.clear_verdicts([key])

    def select_all(self):
        self._selected = set(self.shown_keys())
        self._refresh_cells()

    def select_none(self):
        self._selected.clear()
        self._refresh_cells()

    # -- import ---------------------------------------------------------------

    def _import(self):
        chips = self.with_verdicts()
        if chips:
            self.import_requested.emit(chips, self._source)

    # -- keys -----------------------------------------------------------------

    def keyPressEvent(self, event):
        mods = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier
                                    | Qt.AltModifier)
        key = event.key()
        if mods == Qt.ControlModifier:
            if key == Qt.Key_O:
                self.open_dialog()
            elif key == Qt.Key_S:
                self.save_requested.emit()
            elif key == Qt.Key_Z:
                self.undo_requested.emit()
            elif key == Qt.Key_Y:
                self.redo_requested.emit()
            elif key == Qt.Key_A:
                self.select_all()
            else:
                super().keyPressEvent(event)
            return
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) \
                and key == Qt.Key_Z:
            self.redo_requested.emit()
            return
        if mods == Qt.NoModifier:
            if Qt.Key_1 <= key <= Qt.Key_9:
                index = key - Qt.Key_1
                if index < len(self._classes):
                    self.set_active(self._classes[index])
                return
            if key == Qt.Key_0:
                self.set_active(IGNORE)
                return
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                self.clear_verdicts(self._selected)
                return
            if key == Qt.Key_Escape:
                self.select_none()
                return
        super().keyPressEvent(event)

    # -- settings, closing ----------------------------------------------------

    def _restore_settings(self):
        settings = app_settings()
        for spin, key, fallback in (
                (self.chip_spin, _CHIP_KEY, DEFAULT_CHIP_PX),
                (self.columns_spin, _COLUMNS_KEY, DEFAULT_COLUMNS),
                (self.tolerance_spin, _TOLERANCE_KEY,
                 int(DEFAULT_TOLERANCE_PX))):
            try:
                value = int(settings.value(key, fallback))
            except (TypeError, ValueError):
                value = fallback
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.show_reviewed_check.blockSignals(True)
        self.show_reviewed_check.setChecked(
            str(settings.value(_SHOW_REVIEWED_KEY, "false")).lower()
            == "true")
        self.show_reviewed_check.blockSignals(False)
        geometry = settings.value(_GEOMETRY_KEY)
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except TypeError:
                pass
        else:
            self.resize(1100, 760)

    def save_settings(self):
        settings = app_settings()
        settings.setValue(_CHIP_KEY, self.chip_spin.value())
        settings.setValue(_COLUMNS_KEY, self.columns_spin.value())
        settings.setValue(_TOLERANCE_KEY, self.tolerance_spin.value())
        settings.setValue(_SHOW_REVIEWED_KEY,
                          self.show_reviewed_check.isChecked())
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())

    def closeEvent(self, event):
        self.save_settings()
        self.loader.cancel_all()
        super().closeEvent(event)

    def _say(self, text: str):
        QMessageBox.information(self, "Model Review", text)
