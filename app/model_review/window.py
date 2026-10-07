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
from PyQt5.QtCore import QPointF, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox,
                             QFileDialog, QGridLayout, QHBoxLayout,
                             QInputDialog, QLabel, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy,
                             QSpinBox, QVBoxLayout, QWidget)

from .. import identity, recent
from ..settings_scope import settings as app_settings
from ..snippets import SnippetLoader, snippet_frame
from .centering import find_centre
from .ledger import DEFAULT_TOLERANCE_PX, IGNORE, LedgerIndex
from .source import by_class, match_candidates, read_candidates

CAPTION_PX = 20                 # the strip under each chip's picture
GRID_SPACING = 8
MIN_CELL_PX = 64
DEFAULT_CHIP_PX = 224           # source pixels read around the centre
DEFAULT_COLUMNS = 6
# Chips built at once. A file of thousands is read and matched in a
# blink, but a cell per chip froze the window for seconds and held every
# chip's pixels in memory (measured 2026-10-06); a page at a time, as the
# Snippet Editor's grid does, costs nothing the user can feel.
DEFAULT_PER_PAGE = 60
ALL_CLASSES = None              # the predicted-class box's first entry

SELECTED_COLOR = QColor(255, 255, 255)
IGNORE_COLOR = QColor(150, 150, 150)
EARLIER_SHADE = QColor(0, 0, 0, 150)
TEXT_COLOR = QColor(220, 220, 220)
# The centre mark: where the label will go. Cyan once the centre has been
# moved from the file's, as the Snippet Editor colours what was measured.
MARK_COLOR = QColor(255, 255, 255, 230)
MOVED_MARK_COLOR = QColor(0, 220, 255, 240)
# A ring round the pixel the label will take, four arms outside it, a
# dark line under a light one. Users found a thin cross-hair hard to see
# on grey water (2026-10-07).
MARK_RING_PX = 7                # the ring's radius, on screen
MARK_GAP_PX = 10                # the arms start here from the centre...
MARK_PX = 9                     # ...and run this far
# The window never opens larger than this much of the screen it is on.
SCREEN_FRACTION = 0.9
# Auto-center looks for the object within this fraction of the chip.
AUTO_RADIUS_FRACTION = 0.25

_CHIP_KEY = "model_review/chip_size"
_MARK_KEY = "model_review/centre_mark"
_COLUMNS_KEY = "model_review/columns"
_PER_PAGE_KEY = "model_review/per_page"
_SHOW_REVIEWED_KEY = "model_review/show_reviewed"
_TOLERANCE_KEY = "model_review/tolerance"
_GEOMETRY_KEY = "model_review/geometry"


class ChipCell(QWidget):
    """One chip: its picture, its score, and what it has become."""

    clicked = pyqtSignal(int, object)       # key, keyboard modifiers
    cleared = pyqtSignal(int)               # right-click: take it back
    # Double-click: put the centre here - (key, column, row) in the
    # picture's own pixels.
    recentred = pyqtSignal(int, float, float)

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
        self._centre: tuple | None = None   # (column, row) in the picture
        self._moved = False
        self._mark = True
        # (x0, y0): the source pixel at the picture's top left, so a new
        # centre can be shown on the old picture while the new is read.
        self.frame: tuple | None = None
        self.setCursor(Qt.PointingHandCursor)
        self.set_size(128)

    def set_centre(self, centre: "tuple | None", moved: bool = False):
        """Where the label will go, in the picture's pixels; None until
        the picture is here."""
        self._centre = None if centre is None else (float(centre[0]),
                                                    float(centre[1]))
        self._moved = bool(moved)
        self.update()

    def set_mark(self, on: bool):
        self._mark = bool(on)
        self.update()

    def _picture_geometry(self) -> "tuple | None":
        """(offset x, offset y, scale) of the picture within the cell:
        where its pixel (c, r) is drawn, and how many screen pixels one
        of its pixels covers. None before there is a picture."""
        if self._pixmap is None or self._pixmap.width() == 0:
            return None
        if self._scaled is None or self._scaled.width() != self._px:
            self._scaled = self._pixmap.scaled(
                self._px, self._px, Qt.KeepAspectRatio,
                Qt.SmoothTransformation)
        return ((self._px - self._scaled.width()) / 2.0,
                (self._px - self._scaled.height()) / 2.0,
                self._scaled.width() / self._pixmap.width())

    def picture_point(self, pos) -> "tuple | None":
        """A widget position as (column, row) of the picture, or None off
        it or before it is here."""
        geometry = self._picture_geometry()
        if geometry is None:
            return None
        off_x, off_y, scale = geometry
        col = (pos.x() - off_x) / scale
        row = (pos.y() - off_y) / scale
        if not (0 <= col < self._pixmap.width()
                and 0 <= row < self._pixmap.height()):
            return None
        return col, row

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
        if self._mark and self._centre is not None:
            self._paint_mark(painter)
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

    def _paint_mark(self, painter):
        """A ring round the pixel the label will take, open so the pixel
        itself is not covered, and four arms outside it; a dark line
        under a light one, to read on bright and dark imagery alike."""
        geometry = self._picture_geometry()
        if geometry is None:
            return
        off_x, off_y, scale = geometry
        x = off_x + (self._centre[0] + 0.5) * scale
        y = off_y + (self._centre[1] + 0.5) * scale
        light = MOVED_MARK_COLOR if self._moved else MARK_COLOR
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        for colour, width in ((QColor(0, 0, 0, 210), 4), (light, 2)):
            painter.setPen(QPen(colour, width))
            painter.drawEllipse(QPointF(x, y), MARK_RING_PX, MARK_RING_PX)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                painter.drawLine(
                    QPointF(x + dx * MARK_GAP_PX, y + dy * MARK_GAP_PX),
                    QPointF(x + dx * (MARK_GAP_PX + MARK_PX),
                            y + dy * (MARK_GAP_PX + MARK_PX)))
        painter.setRenderHint(QPainter.Antialiasing, False)

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

    def mouseDoubleClickEvent(self, event):
        """The second press of a double-click (the first was an ordinary
        click, and did what a click does): put the centre here."""
        if event.button() != Qt.LeftButton:
            return
        point = self.picture_point(event.pos())
        if point is not None:
            self.recentred.emit(self.key, point[0], point[1])


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
        self._listed: list = []         # the chips the filters let through
        self._visible: list = []        # the page of them on the grid
        self._page = 0
        self._page_start = 0            # the rank of the page's first chip
        self._source = ""
        self._report = None
        self._classes: list = []
        self._colours: list = []
        self._selected: set = set()
        self._anchor: int | None = None
        self._active: str | None = None
        self._cells: dict = {}
        self._pixels: dict = {}         # key -> the page's chip pictures
        self._note = ""                 # a word for the status line, once
        # Centre moves made here, before an import, so Ctrl+Z can take
        # them back: each entry maps chip key -> (x, y) before the move.
        self._centre_undo: list = []
        self._centre_redo: list = []
        # The last plain click (key, the verdict before it): a double-
        # click's first half, to be undone when the second half arrives.
        self._last_click: tuple | None = None
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
        # A long summary wraps; it never decides how wide the window must
        # be (a window wider than the screen, with no way to shrink it,
        # 2026-10-07).
        self.file_label.setWordWrap(True)
        self.file_label.setSizePolicy(QSizePolicy.Ignored,
                                      QSizePolicy.Preferred)
        self.file_label.setMinimumWidth(0)
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
        top.addWidget(QLabel("Per page"))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(6, 600)
        self.page_spin.setValue(DEFAULT_PER_PAGE)
        self.page_spin.setToolTip(
            "Chips on a page. Next and Previous, or PageDown and PageUp,\n"
            "turn the pages.")
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(self._on_per_page)
        top.addWidget(self.page_spin)
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
        self.show_reviewed_check.toggled.connect(self._on_show_reviewed)
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
        second.addSpacing(12)
        self.mark_check = QCheckBox("Centre mark")
        self.mark_check.setToolTip(
            "Mark where the label will go on each chip: the file's centre,\n"
            "or where you put it. Cyan once it has been moved.")
        self.mark_check.toggled.connect(self._on_mark)
        second.addWidget(self.mark_check)
        self.auto_button = QPushButton("Auto-center")
        self.auto_button.setToolTip(
            "Move each picked chip's centre - or every chip on the page,\n"
            "with none picked - onto the nearest patch that stands out\n"
            "from the background, bright or dark. A first guess to look\n"
            "at: the mark shows where it landed, a double-click or a\n"
            "right-click puts it where you say, and Ctrl+Z takes it back.")
        self.auto_button.clicked.connect(self._auto_centre)
        second.addWidget(self.auto_button)
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
            "number key labels the picked. 0 is Ignore. Double-click the "
            "object to put the label's centre there (it does not label); "
            "Ctrl+Z takes a centre move back. Right-click or Delete takes "
            "a verdict back, and the centre with it.")
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

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.prev_button = QPushButton("< Previous")
        self.prev_button.clicked.connect(lambda: self._step_page(-1))
        bottom.addWidget(self.prev_button)
        self.page_label = QLabel("")
        bottom.addWidget(self.page_label)
        self.next_button = QPushButton("Next >")
        self.next_button.clicked.connect(lambda: self._step_page(1))
        bottom.addWidget(self.next_button)
        layout.addLayout(bottom)
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
        self._last_click = None
        self._centre_undo.clear()
        self._centre_redo.clear()
        self._page = 0
        self.setWindowTitle(f"Model Review - {name}")
        self.file_label.setText(f"{name}: {report.summary()}")
        self.refresh_earlier()          # which also fills the grid
        return True

    def refresh_earlier(self):
        """Ask the project's memory which chips were reviewed before."""
        index = LedgerIndex(getattr(self._project(), "model_review", []),
                            float(self.tolerance_spin.value()))
        for chip in self._chips:
            chip.earlier = index.find(chip.image_name, chip.pixel_x,
                                      chip.pixel_y)
            if chip.earlier is not None:
                chip.verdict = None
        self._fill_filter()
        self._rebuild()

    # -- what is shown --------------------------------------------------------

    def chips(self) -> list:
        return list(self._chips)

    def source(self) -> str:
        return self._source

    def listed(self) -> list:
        """The chips the filters let through, in grid order: every page."""
        return list(self._listed)

    def listed_keys(self) -> list:
        return [chip.key for chip in self._listed]

    def shown(self) -> list:
        """The chips on the grid now: the current page."""
        return list(self._visible)

    def shown_keys(self) -> list:
        return [chip.key for chip in self._visible]

    def page(self) -> int:
        return self._page

    def page_count(self) -> int:
        return max(1, -(-len(self._listed) // max(1, self.page_spin.value())))

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

    def _listed_chips(self) -> list:
        wanted = self.class_filter.currentData()
        show_reviewed = self.show_reviewed_check.isChecked()
        chosen = [chip for chip in self._chips
                  if (wanted is ALL_CLASSES or chip.predicted == wanted)
                  and (show_reviewed or not chip.reviewed_before())]
        return sorted(chosen, key=lambda c: (-c.score, c.key))

    def _step_page(self, delta: int):
        page = max(0, min(self.page_count() - 1, self._page + delta))
        if page != self._page:
            self._page = page
            self._rebuild()

    def _on_per_page(self, value):
        # Keep the place: the chip at the top of the page stays on it.
        self._page = self._page_start // max(1, int(value))
        self._rebuild()

    def _on_show_reviewed(self, _on):
        self._page = 0
        self._fill_filter()             # its counts change with it
        self._rebuild()

    def _cell_px(self) -> int:
        """The cells share the viewport's width, margins and all, so the
        grid never needs a sideways scrollbar."""
        columns = max(1, self.columns_spin.value())
        margins = self._grid.contentsMargins()
        width = (self.scroll.viewport().width() - margins.left()
                 - margins.right() - GRID_SPACING * (columns - 1) - 2)
        return max(MIN_CELL_PX, width // columns)

    def _rebuild(self):
        self.loader.cancel_all()
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cells.clear()
        self._pixels.clear()
        self._listed = self._listed_chips()
        per_page = max(1, self.page_spin.value())
        self._page = max(0, min(self.page_count() - 1, self._page))
        start = self._page_start = self._page * per_page
        self._visible = self._listed[start:start + per_page]
        keys = {chip.key for chip in self._visible}
        self._selected &= keys          # a selection lives on its page
        if self._anchor not in keys:
            self._anchor = None
        self.page_label.setText(
            f"{start + 1}-{start + len(self._visible)} of {len(self._listed)}"
            if self._listed else "none")
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < self.page_count() - 1)
        columns = max(1, self.columns_spin.value())
        px = self._cell_px()
        for i, chip in enumerate(self._visible):
            cell = ChipCell(chip.key)
            cell.set_size(px)
            cell.set_mark(self.mark_check.isChecked())
            self._set_tooltip(cell, chip)
            cell.clicked.connect(self._on_cell_clicked)
            cell.cleared.connect(self._on_cell_cleared)
            cell.recentred.connect(self._on_cell_recentred)
            self._grid.addWidget(cell, i // columns, i % columns)
            self._cells[chip.key] = cell
        self._refresh_cells()
        self._request_pixels()
        self._show_counts()

    @staticmethod
    def _set_tooltip(cell, chip):
        text = (f"{chip.image_name}  ({chip.pixel_x:.1f}, {chip.pixel_y:.1f})"
                f"\nmodel: {chip.predicted} {chip.score:.2f}")
        if chip.rescaled:
            text += "\n(scaled from the size the file stated)"
        if chip.moved():
            text += (f"\ncentre moved {chip.offset():.0f} px from the "
                     f"file's ({chip.source_x:.1f}, {chip.source_y:.1f})")
        cell.setToolTip(text)

    def _request_pixels(self):
        size = self.chip_spin.value()
        for chip in self._visible:
            self.loader.request(chip.key, chip.image_path, chip.pixel_x,
                                chip.pixel_y, size)

    def _frame_of(self, chip) -> tuple:
        """(x0, y0, w, h): the source pixels the chip's picture shows -
        the window around its centre, shifted to stay inside the image,
        exactly as the picture was read."""
        size = self.chip_spin.value()
        if chip.image_width and chip.image_height:
            return snippet_frame(chip.pixel_x, chip.pixel_y, size,
                                 chip.image_width, chip.image_height)
        return (int(round(chip.pixel_x)) - size // 2,
                int(round(chip.pixel_y)) - size // 2, size, size)

    def _on_pixels(self, key, arr):
        cell = self._cells.get(key)
        chip = self._by_key.get(key)
        if cell is None or chip is None or arr is None:
            return
        self._pixels[key] = arr
        cell.set_pixels(arr)
        x0, y0, _w, _h = self._frame_of(chip)
        cell.frame = (x0, y0)
        cell.set_centre((chip.pixel_x - x0, chip.pixel_y - y0), chip.moved())

    def _reload(self, chip):
        """The chip's centre moved: its picture is read again around it,
        so the centre is the middle again. Until it arrives the mark
        moves to the new centre on the old picture, so the move shows at
        once."""
        self._pixels.pop(chip.key, None)
        cell = self._cells.get(chip.key)
        if cell is not None:
            shown = None
            if cell.frame is not None and cell._pixmap is not None:
                col = chip.pixel_x - cell.frame[0]
                row = chip.pixel_y - cell.frame[1]
                if (0 <= col < cell._pixmap.width()
                        and 0 <= row < cell._pixmap.height()):
                    shown = (col, row)
            cell.set_centre(shown, chip.moved())
            self._set_tooltip(cell, chip)
        self.loader.request(chip.key, chip.image_path, chip.pixel_x,
                            chip.pixel_y, self.chip_spin.value())

    def _on_chip_size(self, _value):
        self._request_pixels()

    # -- the centre: where the label will go -------------------------------

    def _on_cell_recentred(self, key, col, row):
        """A double-click on the object: the label goes there - and the
        chip is NOT labelled: the click that began the double-click gave
        it the active label, as a click does, and that is taken back
        (users re-centring found chips labelled under them, 2026-10-07)."""
        self.setFocus()
        chip = self._by_key.get(key)
        if chip is None or chip.reviewed_before():
            return
        if self._last_click is not None and self._last_click[0] == key:
            chip.verdict = self._last_click[1]
            self._last_click = None
        x0, y0, _w, _h = self._frame_of(chip)
        self._remember_centres([chip])
        chip.recenter(x0 + col, y0 + row)
        self._reload(chip)
        self._refresh_cells()
        self._note = f"Centre moved {chip.offset():.0f} px."
        self._show_counts()

    def _remember_centres(self, chips):
        """Before a move: where these chips' centres are, for Ctrl+Z."""
        self._centre_undo.append({chip.key: (chip.pixel_x, chip.pixel_y)
                                  for chip in chips})
        self._centre_redo.clear()

    def _apply_centres(self, positions: dict) -> dict:
        """Put these chips' centres where ``positions`` says; returns
        where they were, for the other stack."""
        replaced = {}
        for key, (x, y) in positions.items():
            chip = self._by_key.get(key)
            if chip is None:
                continue
            replaced[key] = (chip.pixel_x, chip.pixel_y)
            chip.recenter(x, y)
            if key in self._cells:
                self._reload(chip)
        self._refresh_cells()
        return replaced

    def undo_centre(self) -> bool:
        """Take the last centre move back. False when there is none, and
        the main window's history is the thing to undo."""
        if not self._centre_undo:
            return False
        self._centre_redo.append(self._apply_centres(self._centre_undo.pop()))
        self._note = "Centre move taken back."
        self._show_counts()
        return True

    def redo_centre(self) -> bool:
        if not self._centre_redo:
            return False
        self._centre_undo.append(self._apply_centres(self._centre_redo.pop()))
        self._note = "Centre move done again."
        self._show_counts()
        return True

    def _auto_centre(self):
        """The picked chips - or the page, with none picked - re-centred
        on the nearest thing that stands out (centering.py)."""
        self.setFocus()
        keys = self._selected or set(self.shown_keys())
        unchanged, waiting = 0, 0
        moves = []                      # (chip, new x, new y)
        radius = self.chip_spin.value() * AUTO_RADIUS_FRACTION
        for key in self.shown_keys():
            if key not in keys:
                continue
            chip = self._by_key.get(key)
            arr = self._pixels.get(key)
            if chip is None or chip.reviewed_before():
                continue
            if arr is None:
                waiting += 1
                continue
            x0, y0, _w, _h = self._frame_of(chip)
            found = find_centre(arr.mean(axis=2), (chip.pixel_x - x0,
                                                   chip.pixel_y - y0), radius)
            if found is None:
                unchanged += 1
                continue
            new_x, new_y = x0 + found[0], y0 + found[1]
            if abs(new_x - chip.pixel_x) < 0.5 \
                    and abs(new_y - chip.pixel_y) < 0.5:
                unchanged += 1
                continue
            moves.append((chip, new_x, new_y))
        if moves:
            # One Ctrl+Z takes the whole run back.
            self._remember_centres([chip for chip, _x, _y in moves])
            for chip, new_x, new_y in moves:
                chip.recenter(new_x, new_y)
                self._reload(chip)
        moved = len(moves)
        parts = [f"Auto-center: {moved} chip{'' if moved == 1 else 's'} "
                 "moved"]
        if unchanged:
            parts.append(f"{unchanged} left, nothing clearer to move to")
        if waiting:
            parts.append(f"{waiting} not yet drawn")
        self._note = "; ".join(parts) + "."
        self._show_counts()

    def _on_mark(self, on):
        for cell in self._cells.values():
            cell.set_mark(on)

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
        listed = len(self._listed)
        left = sum(1 for chip in self._listed
                   if chip.verdict is None and not chip.reviewed_before())
        text = (f"{listed} chip{'' if listed == 1 else 's'} listed, "
                f"{len(self._visible)} on this page: {left} to look at, "
                f"{labelled} labelled, {ignored} to ignore")
        if earlier:
            text += (f"; {earlier} reviewed in an earlier round"
                     + ("" if self.show_reviewed_check.isChecked()
                        else ", hidden"))
        if self._note:
            text += "  " + self._note
            self._note = ""
        self.status.setText(text)

    def _on_filter(self, _index):
        self._page = 0
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
        """Take the verdict back - and the centre, to the file's."""
        chips = [self._by_key[key] for key in list(keys)
                 if key in self._by_key]
        moved = [chip for chip in chips if chip.moved()]
        if moved:
            self._remember_centres(moved)
        for chip in chips:
            chip.verdict = None
            if chip.moved():
                chip.restore_centre()
                self._reload(chip)
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
            self._last_click = None
        else:
            run = [key]
            # Should this turn out to be the first half of a double-click,
            # the verdict it gives is taken back (_on_cell_recentred).
            self._last_click = (key, self._by_key[key].verdict)
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
            # The centres go into the project with the labels; from here
            # Ctrl+Z is the project's history.
            self._centre_undo.clear()
            self._centre_redo.clear()
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
                # A centre move made here first; then the project's history.
                if not self.undo_centre():
                    self.undo_requested.emit()
            elif key == Qt.Key_Y:
                if not self.redo_centre():
                    self.redo_requested.emit()
            elif key == Qt.Key_A:
                self.select_all()
            else:
                super().keyPressEvent(event)
            return
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) \
                and key == Qt.Key_Z:
            if not self.redo_centre():
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
            if key == Qt.Key_PageDown:
                self._step_page(1)
                return
            if key == Qt.Key_PageUp:
                self._step_page(-1)
                return
        super().keyPressEvent(event)

    # -- settings, closing ----------------------------------------------------

    def _restore_settings(self):
        settings = app_settings()
        for spin, key, fallback in (
                (self.chip_spin, _CHIP_KEY, DEFAULT_CHIP_PX),
                (self.columns_spin, _COLUMNS_KEY, DEFAULT_COLUMNS),
                (self.page_spin, _PER_PAGE_KEY, DEFAULT_PER_PAGE),
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
        self.mark_check.setChecked(
            str(settings.value(_MARK_KEY, "true")).lower() != "false")
        geometry = settings.value(_GEOMETRY_KEY)
        restored = False
        if geometry is not None:
            try:
                restored = self.restoreGeometry(geometry)
            except TypeError:
                pass
        if not restored:
            self.resize(1100, 760)
        self._fit_to_screen()

    def _fit_to_screen(self, available=None):
        """No larger than most of the screen, and on it: a window that
        opened taller than a laptop screen had its edges out of reach
        (2026-10-07). ``available`` is the screen's usable rectangle;
        the primary screen's when not given."""
        if available is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
        width = min(self.width(), int(available.width() * SCREEN_FRACTION))
        height = min(self.height(),
                     int(available.height() * SCREEN_FRACTION))
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        # Left and top edges on the screen first: a window that is still
        # too big for it (its controls set a minimum) keeps its handles.
        x = max(available.left(), min(self.x(), available.right() - width + 1))
        y = max(available.top(), min(self.y(), available.bottom() - height + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def save_settings(self):
        settings = app_settings()
        settings.setValue(_CHIP_KEY, self.chip_spin.value())
        settings.setValue(_COLUMNS_KEY, self.columns_spin.value())
        settings.setValue(_PER_PAGE_KEY, self.page_spin.value())
        settings.setValue(_TOLERANCE_KEY, self.tolerance_spin.value())
        settings.setValue(_SHOW_REVIEWED_KEY,
                          self.show_reviewed_check.isChecked())
        settings.setValue(_MARK_KEY, self.mark_check.isChecked())
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())

    def closeEvent(self, event):
        self.save_settings()
        self.loader.cancel_all()
        super().closeEvent(event)

    def _say(self, text: str):
        QMessageBox.information(self, "Model Review", text)
