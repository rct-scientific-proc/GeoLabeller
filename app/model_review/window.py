"""The Model Review window: the model's mistakes in a grid, labelled.

Opened from Labels > Model Review... It reads a file of chips (source.py),
shows the ones the model called one class from most to least sure, top
left to bottom right, and takes verdicts: a click gives a chip the
active label - a class from the New label box, or Ignore - Shift-click a
run of them, Ctrl-click picks several for a key to label at once. Or
without the mouse: the arrow keys move a cursor from chip to chip, a
number key or Enter labels the chip under it and moves on, I ignores the
rest of the page. Import to project (Ctrl+Enter) hands the verdicts to
the main window, which makes the labels in one undo step; what each
chip became is written into the review file itself (outcomes.py), and
the chips then leave the grid, and stay off it when the file is opened
again, unless Show reviewed. A chip whose centre lies within a distance
of a label the project already has - on its image, not its own - is
labelled already (labelled.py): shown apart, off the grid unless Show
labelled already, the labels marked in Details, and a verdict on it
goes onto that label unless the user asks for a new one beside it. The
comparison is made again whenever the project's labels change.

Ctrl+Z here takes back the last thing done in this window - a verdict, a
run of them, a centre move - and only then reaches the project's
history. The chip size is the user's, as in the Snippet Editor: the file
says only where the centre is.

Nothing here touches the project: the window reads it (through the
getter it is given, so it is always the current one) and reports what
the user decided through signals.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox,
                             QFileDialog, QGridLayout, QHBoxLayout,
                             QInputDialog, QLabel, QMessageBox,
                             QDoubleSpinBox, QPushButton, QScrollArea,
                             QSizePolicy, QSpinBox, QSplitter,
                             QToolButton, QVBoxLayout, QWidget)

from .. import identity, recent
from ..settings_scope import settings as app_settings
from ..shortcuts import SHIFTED_DIGITS
from ..snippets import SnippetLoader, snippet_frame
from .centering import find_centre
from .class_order import load_order, ordered, save_order, with_rename
from .detail import DEFAULT_CONTEXT, DetailPanel, standing_of
from .image_window import ImageWindow
from .labels_panel import IGNORE_COLOR, LabelsPanel
from .learn_window import LearnWindow
from .learner import (BACKBONES, RESNET18, Learner,
                      available as learning_available, backbone_reason,
                      checked as learning_checked, gather_examples,
                      name_of, readiness)
from .reference import DEFAULT_COLUMNS as DEFAULT_REF_COLUMNS
from .reference import MAX_SHOWN, ReferencePanel
from .snippet_store import SnippetStore
from .training import LearnThread, save_job, suggest_job, train_job
from .ledger import DEFAULT_TOLERANCE_PX, IGNORE, LedgerIndex, Verdict
from . import labelled, outcomes
from .source import (CANDIDATE,
                     by_class, called, match_candidates, read_candidates)

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
UNDO_LIMIT = 200

SELECTED_COLOR = QColor(255, 255, 255)
CURSOR_COLOR = QColor(255, 200, 0)
HOVER_COLOR = QColor(255, 255, 255, 110)
EARLIER_SHADE = QColor(0, 0, 0, 150)
# A chip labelled already (2.9.4): its frame and caption, unlike any
# class's colour and unlike the shade of a chip reviewed before.
LABELLED_COLOR = QColor(80, 200, 255)
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
_SHOW_LABELLED_KEY = "model_review/show_labelled"
_LABELLED_KEY = "model_review/labelled_px"
_MORE_KEY = "model_review/more"
_LEARN_KEY = "model_review/learn"
_REF_COLUMNS_KEY = "model_review/reference_columns"
_MIN_SCORE_KEY = "model_review/min_score"
_GEOMETRY_KEY = "model_review/geometry"
_DETAILS_KEY = "model_review/details"
_CONTEXT_KEY = "model_review/context"
# Three panes since the Labels panel (2.9.7): a state saved for two
# would hand the panel the grid's old width, so the old key is dropped.
_SPLITTER_KEY = "model_review/splitter3"
_OLD_SPLITTER_KEY = "model_review/splitter"
_LABELS_KEY = "model_review/labels_panel"
HOVER_MS = 250                  # the mouse rests on a chip this long
                                # before the panel turns to it
SCROLL_MS = 120                 # a scroll settles this long before the
                                # queued reads are put in its order
VERDICT_FILTERS = (("All", "all"), ("To look at", "todo"),
                   ("Labelled", "labelled"), ("Ignored", "ignored"))
# The Show: list's standings (2.9.9): each lists exactly those chips,
# whatever the Show reviewed / Show labelled already boxes say - a
# view of the done pile, or of what matches a label already there,
# must not come up empty because a box is off.
STANDING_FILTERS = (("Reviewed before", "reviewed"),
                    ("Changed", "changed"),
                    ("Already in project", "already"))
STANDINGS = tuple(data for _text, data in STANDING_FILTERS)
ORDERS = (("Model score", "score"), ("Suggestion", "suggestion"))
ANY_SUGGESTION = None           # the Suggested box's first entry
DEFAULT_ACCEPT = 0.90           # a suggestion at least this sure is taken
_ACCEPT_KEY = "model_review/accept"
_BACKBONE_KEY = "model_review/backbone"
_RIGHT_KEY = "model_review/right_splitter"
WORK_WAIT_MS = 5000             # closing waits this long for the work
LOOKING = "Looking for PyTorch..."


class ChipCell(QWidget):
    """One chip: its picture, its score, and what it has become."""

    clicked = pyqtSignal(int, object)       # key, keyboard modifiers
    cleared = pyqtSignal(int)               # right-click: take it back
    double_clicked = pyqtSignal(int)        # its second press, whatever
                                            # the picture
    hovered = pyqtSignal(int)               # the mouse came onto it...
    left = pyqtSignal(int)                  # ...and went again
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
        self._cursor = False
        self._hover = False
        self._earlier = False
        self._labelled = False          # labelled already (2.9.4)
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
                  selected: bool, earlier: bool, cursor: bool = False,
                  labelled: bool = False):
        self._score, self._verdict, self._colour = score, verdict, colour
        self._selected, self._earlier = selected, earlier
        self._cursor = cursor
        self._labelled = labelled
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
        if border is None and self._labelled:
            border = LABELLED_COLOR
        if border is not None:
            painter.setPen(QPen(border, 3))
            painter.drawRect(picture.adjusted(1, 1, -2, -2))
        elif self._hover:
            painter.setPen(QPen(HOVER_COLOR, 1))
            painter.drawRect(picture.adjusted(0, 0, -1, -1))
        if self._labelled and border is not LABELLED_COLOR:
            # Given a verdict, or picked: the frame is the verdict's,
            # and a line inside it still says labelled already.
            painter.setPen(QPen(LABELLED_COLOR, 2))
            painter.drawRect(picture.adjusted(5, 5, -6, -6))
        if self._cursor:
            # The keyboard's chip: a dashed line just inside the edge, on
            # top of whatever frame the chip wears.
            pen = QPen(CURSOR_COLOR, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(picture.adjusted(4, 4, -5, -5))
        caption = QRect(0, self._px, self._px, CAPTION_PX)
        painter.setPen(TEXT_COLOR)
        metrics = painter.fontMetrics()
        room = self._px - 8 if not self._verdict else int(self._px * 0.55)
        painter.drawText(caption.adjusted(4, 0, -4, 0),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(self._score, Qt.ElideRight,
                                            max(20, room)))
        if self._verdict:
            painter.setPen(self._colour or TEXT_COLOR)
            painter.drawText(caption.adjusted(4, 0, -4, 0),
                             Qt.AlignRight | Qt.AlignVCenter,
                             metrics.elidedText(self._verdict, Qt.ElideRight,
                                                max(20, self._px - room - 8)))
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

    def enterEvent(self, event):
        self._hover = True
        self.update()
        self.hovered.emit(self.key)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        self.left.emit(self.key)
        super().leaveEvent(event)

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
        self.double_clicked.emit(self.key)
        point = self.picture_point(event.pos())
        if point is not None:
            self.recentred.emit(self.key, point[0], point[1])


def _swatch(colour: QColor) -> QIcon:
    """A small square of a class's colour, for the New label box."""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor(40, 40, 40), 1))
    painter.setBrush(colour)
    painter.drawRect(1, 1, 11, 11)
    painter.end()
    return QIcon(pixmap)


def pending(chip) -> bool:
    """Has an import something to do for the chip: a verdict given
    here, or a chip reviewed before re-centred (2.8.11) - the label it
    made moves. One that made no label has nothing to move."""
    if chip.verdict is not None:
        return True
    return (chip.reviewed_before() and chip.moved_since_import()
            and chip.earlier.became != IGNORE)


def effective_verdict(chip):
    """The class an import gives the chip: its verdict, or what it
    became in an earlier round."""
    if chip.verdict is not None:
        return chip.verdict
    return chip.earlier.became if chip.reviewed_before() else None


def _passes(chip, standing: str) -> bool:
    """Does the chip belong on the Show: list? A chip reviewed in an
    earlier round counts by what it became - unless given a new
    verdict here (2.8.5)."""
    if standing == "reviewed":
        return chip.reviewed_before()
    if standing == "changed":
        return chip.reviewed_before() and chip.verdict is not None
    if standing == "already":
        return chip.labelled_already()
    verdict = chip.verdict
    if verdict is None and chip.reviewed_before():
        verdict = chip.earlier.became
    if standing and standing.startswith("labelled:"):
        return verdict == standing[len("labelled:"):]
    if standing == "todo":
        return verdict is None
    if standing == "labelled":
        return verdict is not None and verdict != IGNORE
    if standing == "ignored":
        return verdict == IGNORE
    return True


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
        self._below_score = 0           # ...and how many the score floor hid
        self._visible: list = []        # the page of them on the grid
        self._page_start = 0            # the rank of the page's first chip
        self._page = 0                  # its number, from that
        self._source = ""
        self._path = ""                 # the file, to write its notes
        self._write_failed = False      # said once per file
        self._misplaced_said = False    # likewise, records that changed
        self._stale = False             # the labels changed while hidden
        self._report = None
        self._classes: list = []
        self._colours: list = []            # by the project's order
        self._project_classes: list = []
        self._colour_by_name: dict = {}
        self._selected: set = set()
        self._anchor: int | None = None
        self._cursor: int | None = None  # the keyboard's chip, on the page
        self._active: str | None = None
        self._cells: dict = {}
        self._pixels: dict = {}         # key -> the page's chip pictures
        self._frames: dict = {}         # key -> (x0, y0) of that picture
        self._note = ""                 # a word for the status line, once
        self._discard_on_close = False  # the project is being replaced
        # What was done here and not yet imported, so Ctrl+Z can take it
        # back: each entry holds, per chip key, (verdict, x, y) before.
        self._undo: list = []
        self._redo: list = []
        # The last plain click (key, the verdict before it, its undo
        # entry): a double-click's first half, undone when the second
        # half arrives.
        self._last_click: tuple | None = None
        self._detail_key: int | None = None     # the panel's chip
        self._hover_key: int | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_MS)
        self._hover_timer.timeout.connect(self._on_hover_rest)
        self._image_window: ImageWindow | None = None
        # Learning (learner.py, training.py): the verdicts taught to a
        # model, which suggests for what is left; chips through the
        # snippet store; the training window shows the work.
        self._learner: Learner | None = None
        self._working: LearnThread | None = None
        self._work_kind = ""                # "train", "suggest", "save"
        self._suggestions: dict = {}        # key -> Suggestion
        self._learn_report = None
        self._learn_progress = ""
        # (device, why not) - or LOOKING until the probe begun at
        # startup (main.py), or here, reports back.
        self._learning = learning_checked() or (None, LOOKING)
        self._probe: LearnThread | None = None
        self._verdict_seq: dict = {}        # key -> when its verdict came
        self._tick = 0
        self._ref_gen = 0                   # the reference strip's reads
        self._reference_dirty = True        # the verdicts changed since
        self._chips_gen = 0                 # bumped by each file opened
        self._work_gen = 0                  # the file the work is for
        self._store = SnippetStore()
        self._learn_window: LearnWindow | None = None
        self.loader = SnippetLoader(self)
        self.loader.ready.connect(self._on_pixels)
        self._setup_ui()
        self._restore_settings()
        self._start_probe()

    # -- layout ---------------------------------------------------------------

    def _titled_row(self, title: str, layout: QVBoxLayout) -> QHBoxLayout:
        """A row of controls behind a bold name: File, Labelling, View."""
        row = QHBoxLayout()
        label = QLabel(title)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        label.setFixedWidth(68)
        row.addWidget(label)
        layout.addLayout(row)
        return row

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # -- File: the team's file, and what came of it ---------------------
        top = self._titled_row("File", layout)
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

        # -- Labelling: the verdicts ----------------------------------------
        second = self._titled_row("Labelling", layout)
        second.addWidget(QLabel("New label:"))
        self.label_combo = QComboBox()
        self.label_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.label_combo.setMinimumWidth(160)
        self.label_combo.setToolTip(
            "The class a click gives a chip. Keys 1-9 pick by place in\n"
            "the list: onto the picked chips, or the chip under the\n"
            "cursor, which then moves on.")
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
        self.ignore_rest_button = QPushButton("Ignore the rest of the page (I)")
        self.ignore_rest_button.setToolTip(
            "Every chip on this page not yet given a verdict is ignored,\n"
            "and the next page comes up. Ctrl+Z takes it back.")
        self.ignore_rest_button.clicked.connect(self.ignore_rest_of_page)
        second.addWidget(self.ignore_rest_button)
        second.addSpacing(12)
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

        # -- View: what the grid shows, and how ---------------------------
        third = self._titled_row("View", layout)
        third.addWidget(QLabel("Predicted:"))
        self.class_filter = QComboBox()
        # Entries come after the window is shown, and the box sized
        # itself once, on an empty list: it cut the names (2026-10-14).
        self.class_filter.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.class_filter.setToolTip(
            "Which of the model's calls to look at. Each list runs from\n"
            "the chip it was surest of to the one it was least.")
        self.class_filter.setMinimumWidth(160)
        self.class_filter.currentIndexChanged.connect(self._on_filter)
        # A control used hands the keys back to the window, so 1-9 and 0
        # go on working without a click on a chip first.
        self.class_filter.activated.connect(lambda _i: self.setFocus())
        third.addWidget(self.class_filter)
        third.addWidget(QLabel("Show:"))
        self.verdict_filter = QComboBox()
        self.verdict_filter.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        for text, data in VERDICT_FILTERS:
            self.verdict_filter.addItem(text, data)
        self.verdict_filter.setToolTip(
            "Which chips the grid lists: all of them, the ones still to\n"
            "look at, the labelled - all, or one class - or the ignored;\n"
            "or one standing: Reviewed before (the done pile, with a note\n"
            "from an earlier import), Changed (an earlier answer with a\n"
            "new verdict in hand), Already in project (a label within the\n"
            "distance on the image) - those three whatever the Show\n"
            "boxes say. The list settles at the next page turn, so a chip\n"
            "just labelled stays put till then.")
        self.verdict_filter.currentIndexChanged.connect(self._on_filter)
        self.verdict_filter.activated.connect(lambda _i: self.setFocus())
        third.addWidget(self.verdict_filter)
        third.addWidget(QLabel("Score at least"))
        self.score_spin = QDoubleSpinBox()
        self.score_spin.setRange(0.0, 1.0)
        self.score_spin.setSingleStep(0.05)
        self.score_spin.setDecimals(2)
        self.score_spin.setValue(0.0)
        self.score_spin.setToolTip(
            "Only the chips the model scored at least this: 0.95 for the\n"
            "ones it was surest of, 0 for all of them. The status bar\n"
            "counts what it hides. Remembered.")
        self.score_spin.setKeyboardTracking(False)
        self.score_spin.valueChanged.connect(self._on_filter)
        self.score_spin.editingFinished.connect(self.setFocus)
        third.addWidget(self.score_spin)
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
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 16)
        self.columns_spin.setValue(DEFAULT_COLUMNS)
        self.columns_spin.setKeyboardTracking(False)
        self.columns_spin.valueChanged.connect(lambda _v: self._rebuild())
        self.page_spin = QSpinBox()
        self.page_spin.setRange(6, 600)
        self.page_spin.setValue(DEFAULT_PER_PAGE)
        self.page_spin.setToolTip(
            "Chips on a page. Next and Previous, or PageDown and PageUp,\n"
            "turn the pages.")
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(self._on_per_page)
        third.addSpacing(12)
        self.mark_check = QCheckBox("Centre mark")
        self.mark_check.setToolTip(
            "Mark where the label will go on each chip: the file's centre,\n"
            "or where you put it. Cyan once it has been moved.")
        self.mark_check.toggled.connect(self._on_mark)
        third.addWidget(self.mark_check)
        self.details_check = QCheckBox("Details")
        self.details_check.setToolTip(
            "A panel beside the grid: the chip in hand - under the cursor,\n"
            "last clicked, or under the mouse - read larger with its\n"
            "surroundings, and everything known of it.")
        self.details_check.setChecked(True)
        self.details_check.toggled.connect(self._on_details)
        third.addWidget(self.details_check)
        self.labels_check = QCheckBox("Labels")
        self.labels_check.setToolTip(
            "A panel on the left of the grid with every class stacked:\n"
            "click one and it is the active label, onto the picked chips\n"
            "or the chip under the cursor. Type in its box to shorten the\n"
            "list; Enter takes the first match. Drag a class up or down\n"
            "to put it under the key you want.")
        self.labels_check.setChecked(True)
        self.labels_check.toggled.connect(self._on_labels_panel)
        third.addWidget(self.labels_check)
        third.addSpacing(12)
        self.more_button = self._fold_button(
            "Settings",
            "Set once: the chip size, the columns, the chips per page,\n"
            "the chips reviewed before and those already in project, and\n"
            "how close a chip must lie to a label to count as already in\n"
            "project.",
            self._on_more)
        third.addWidget(self.more_button)
        self.learn_button = self._fold_button(
            "Learn",
            "A model from the verdicts, to sort what is left: the\n"
            "training, and the suggestions it makes. Needs a source run\n"
            "with PyTorch (requirements-ml.txt).", self._on_learn)
        third.addWidget(self.learn_button)
        third.addStretch(1)
        for spin in (self.chip_spin, self.columns_spin, self.page_spin):
            spin.editingFinished.connect(self.setFocus)

        self.more_row = QWidget()
        more_box = QVBoxLayout(self.more_row)
        more_box.setContentsMargins(0, 0, 0, 0)
        more = self._titled_row("Settings", more_box)
        more.addWidget(QLabel("Chip size"))
        more.addWidget(self.chip_spin)
        more.addWidget(QLabel("Columns"))
        more.addWidget(self.columns_spin)
        more.addWidget(QLabel("Per page"))
        more.addWidget(self.page_spin)
        more.addSpacing(12)
        self.show_reviewed_check = QCheckBox("Show reviewed")
        self.show_reviewed_check.setToolTip(
            "Chips the file says were reviewed - imported, or ignored -\n"
            "shaded, with what they became. Kept off the grid otherwise.\n"
            "Give one a new verdict - a click, a number - and Import\n"
            "changes the label it made.")
        self.show_reviewed_check.toggled.connect(self._on_show_reviewed)
        more.addWidget(self.show_reviewed_check)
        # Labelled already (2.9.4): a label on the image within this
        # distance of the chip's centre, not the chip's own.
        self.show_labelled_check = QCheckBox("Show already in project")
        self.show_labelled_check.setToolTip(
            "Chips already in the project: a label on their image within\n"
            "the distance - placed by hand, or by another round. Framed\n"
            "in blue, with the label's class and how far off it is;\n"
            "Details marks the labels. Untick to clear them off the grid.\n"
            "A verdict on one goes onto that label, or makes a new one\n"
            "beside it when Details says so; Ignore leaves it alone.")
        self.show_labelled_check.toggled.connect(self._on_show_labelled)
        more.addWidget(self.show_labelled_check)
        more.addWidget(QLabel("Labelled within"))
        self.labelled_spin = QSpinBox()
        self.labelled_spin.setRange(1, 500)
        self.labelled_spin.setValue(int(round(labelled.default_distance())))
        self.labelled_spin.setSuffix(" px")
        self.labelled_spin.setToolTip(
            "A chip whose centre lies this close to a label on its image\n"
            "is already in project. The Import Options distance to start\n"
            "with; remembered.")
        self.labelled_spin.setKeyboardTracking(False)
        self.labelled_spin.valueChanged.connect(
            lambda _v: self.refresh_earlier())
        self.labelled_spin.editingFinished.connect(self.setFocus)
        more.addWidget(self.labelled_spin)
        more.addWidget(QLabel("Same chip within"))
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(1, 500)
        self.tolerance_spin.setValue(int(DEFAULT_TOLERANCE_PX))
        self.tolerance_spin.setSuffix(" px")
        self.tolerance_spin.setToolTip(
            "How close a candidate of a new detector run must lie to one\n"
            "of the last run's to be it again, and how close a chip to an\n"
            "entry of a project's old memory (before 2.9.4) to take it.")
        self.tolerance_spin.setKeyboardTracking(False)
        self.tolerance_spin.editingFinished.connect(self.setFocus)
        more.addWidget(self.tolerance_spin)
        more.addStretch(1)
        self.more_row.setVisible(False)
        layout.addWidget(self.more_row)

        # -- Learn: a model from the verdicts, to sort what is left - two
        # rows folded away behind the View row's Learn button (2.8.2),
        # since the installed build has no PyTorch to use them with.
        self.learn_rows = QWidget()
        learn_box = QVBoxLayout(self.learn_rows)
        learn_box.setContentsMargins(0, 0, 0, 0)
        fourth = self._titled_row("Learn", learn_box)
        self.train_button = QPushButton("Train on verdicts")
        self.train_button.setToolTip(
            "A small classifier trained on the verdicts given here, the\n"
            "project's memory of earlier rounds and its labels of those\n"
            "classes, Ignore counting as a class. Every chip still to look\n"
            "at then gets a suggested class and a confidence. A fifth of\n"
            "the chips is held out, and the accuracy reported is on those.\n"
            "Needs a source run with PyTorch (requirements-ml.txt).")
        self.train_button.clicked.connect(self.train)
        fourth.addWidget(self.train_button)
        self.suggest_button = QPushButton("Suggest")
        self.suggest_button.setToolTip(
            "Suggestions again from the model as trained - after a\n"
            "chip-size change, or for the next round's file - without\n"
            "training again.")
        self.suggest_button.clicked.connect(self.suggest)
        fourth.addWidget(self.suggest_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_work)
        self.stop_button.setVisible(False)
        fourth.addWidget(self.stop_button)
        fourth.addSpacing(12)
        fourth.addWidget(QLabel("Backbone:"))
        self.backbone_combo = QComboBox()
        for name, text in BACKBONES:
            self.backbone_combo.addItem(text, name)
        self.backbone_combo.setToolTip(
            "The small net learns from scratch and needs nothing more.\n"
            "ResNet18 brings ImageNet's eyes - torchvision, and a 45 MB\n"
            "weights file fetched once - and does better with few\n"
            "verdicts.")
        why = backbone_reason(RESNET18)
        if why:
            index = self.backbone_combo.findData(RESNET18)
            self.backbone_combo.model().item(index).setEnabled(False)
            self.backbone_combo.setItemData(index, why, Qt.ToolTipRole)
        self.backbone_combo.activated.connect(lambda _i: self.setFocus())
        fourth.addWidget(self.backbone_combo)
        fourth.addSpacing(12)
        self.save_button = QPushButton("Save snippets")
        self.save_button.setToolTip(
            "Read every chip of the file, and everything there is to\n"
            "learn from, into the snippet store now - small, under the\n"
            "system's temporary folder - so training and suggesting read\n"
            "them and not the imagery. Done anyway as chips are needed;\n"
            "this does it ahead.")
        self.save_button.clicked.connect(self.save_snippets)
        fourth.addWidget(self.save_button)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Delete the saved snippets.")
        self.clear_button.clicked.connect(self.clear_snippets)
        fourth.addWidget(self.clear_button)
        self.results_button = QPushButton("Results...")
        self.results_button.setToolTip(
            "The training window: the progress while it runs, the\n"
            "report after.")
        self.results_button.clicked.connect(self.show_results)
        fourth.addWidget(self.results_button)
        self.learn_label = QLabel("")
        self.learn_label.setWordWrap(True)
        self.learn_label.setSizePolicy(QSizePolicy.Ignored,
                                       QSizePolicy.Preferred)
        self.learn_label.setMinimumWidth(0)
        fourth.addWidget(self.learn_label, 1)

        # -- Suggestions: what the model thinks, on the grid -------------
        fifth = self._titled_row("Suggestions", learn_box)
        fifth.addWidget(QLabel("Order by:"))
        self.order_combo = QComboBox()
        for text, data in ORDERS:
            self.order_combo.addItem(text, data)
        self.order_combo.setToolTip(
            "The grid's order: the model's score, or the suggestion's\n"
            "confidence - the surest first either way.")
        self.order_combo.currentIndexChanged.connect(self._on_filter)
        self.order_combo.activated.connect(lambda _i: self.setFocus())
        fifth.addWidget(self.order_combo)
        fifth.addWidget(QLabel("Suggested:"))
        self.suggested_filter = QComboBox()
        self.suggested_filter.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.suggested_filter.setToolTip(
            "Only the chips the model suggests this class for.")
        self.suggested_filter.currentIndexChanged.connect(self._on_filter)
        self.suggested_filter.activated.connect(lambda _i: self.setFocus())
        fifth.addWidget(self.suggested_filter)
        fifth.addSpacing(12)
        self.accept_button = QPushButton("Accept (S)")
        self.accept_button.setToolTip(
            "The suggested class onto the picked chips, or onto the chip\n"
            "under the cursor, which then moves on.")
        self.accept_button.clicked.connect(self.accept_suggestions)
        fifth.addWidget(self.accept_button)
        self.accept_page_button = QPushButton("Accept the page at or above")
        self.accept_page_button.setToolTip(
            "Every chip on this page still without a verdict whose\n"
            "suggestion is at least this sure gets it - one thing to take\n"
            "back with Ctrl+Z.")
        self.accept_page_button.clicked.connect(self.accept_page)
        fifth.addWidget(self.accept_page_button)
        self.accept_spin = QDoubleSpinBox()
        self.accept_spin.setRange(0.5, 0.99)
        self.accept_spin.setSingleStep(0.05)
        self.accept_spin.setDecimals(2)
        self.accept_spin.setValue(DEFAULT_ACCEPT)
        self.accept_spin.setKeyboardTracking(False)
        self.accept_spin.editingFinished.connect(self.setFocus)
        fifth.addWidget(self.accept_spin)
        fifth.addStretch(1)
        self.learn_rows.setVisible(False)
        layout.addWidget(self.learn_rows)
        self._fill_suggested_filter()
        self._show_learning()

        self.hint = QLabel(
            "Click a chip, or press its label's number with the cursor on "
            "it, to label it; arrows move the cursor, Enter labels and "
            "moves on, I ignores the rest of the page, M shows the chip on "
            "its image. F1 lists every key.")
        self.hint.setWordWrap(True)
        self.hint.setEnabled(False)          # a hint, in grey
        layout.addWidget(self.hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self._grid_host)
        # A scroll brings other rows into view: the reads still queued
        # for the page go on in the new order (2.9.10).
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(SCROLL_MS)
        self._scroll_timer.timeout.connect(self._refill_in_view)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: self._scroll_timer.start())
        self.scroll.viewport().installEventFilter(self)
        self.detail = DetailPanel()
        self.detail.show_on_image.connect(self.show_on_image)
        self.detail.as_new_changed.connect(self._on_as_new)
        self.detail.context_changed.connect(
            lambda _v: self._request_detail())
        self.detail.context_spin.editingFinished.connect(self.setFocus)
        self.reference = ReferencePanel()
        self.reference.class_changed.connect(
            lambda: self._refresh_reference(force=True))
        self.reference.columns_spin.editingFinished.connect(self.setFocus)
        self.right = QSplitter(Qt.Vertical)
        self.right.addWidget(self.detail)
        self.right.addWidget(self.reference)
        self.right.setStretchFactor(0, 3)
        self.right.setStretchFactor(1, 2)
        # The Labels panel (labels_panel.py, 2.9.7): every class
        # stacked on the left, a click away.
        self.labels_panel = LabelsPanel()
        self.labels_panel.picked.connect(self._on_panel_pick)
        self.labels_panel.released.connect(self.setFocus)
        self.labels_panel.reordered.connect(self._on_panel_reordered)
        self.labels_panel.add_requested.connect(self._add_class)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.labels_panel)
        self.splitter.addWidget(self.scroll)
        self.splitter.addWidget(self.right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        for i in range(3):
            self.splitter.setCollapsible(i, False)
        layout.addWidget(self.splitter, 1)

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
        bottom.addSpacing(16)
        # Where every page's work ends: the one action that matters.
        self.import_button = QPushButton("Import to project")
        font = self.import_button.font()
        font.setBold(True)
        self.import_button.setFont(font)
        self.import_button.setMinimumHeight(32)
        self.import_button.setToolTip(
            "Every labelled chip becomes a label on its image, with what\n"
            "the model said in its description; ignored chips make none.\n"
            "One undo step. The chips then leave the grid. Ctrl+Enter.")
        self.import_button.clicked.connect(self._import)
        bottom.addWidget(self.import_button)
        layout.addLayout(bottom)
        self._show_counts()

    @staticmethod
    def _fold_button(text: str, tip: str, slot) -> QToolButton:
        """A checkable arrow button that folds a row out below."""
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setArrowType(Qt.RightArrow)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setToolTip(tip)
        button.toggled.connect(slot)
        return button

    def _on_more(self, on: bool):
        self.more_row.setVisible(bool(on))
        self.more_button.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    def _on_learn(self, on: bool):
        self.learn_rows.setVisible(bool(on))
        self.learn_button.setArrowType(Qt.DownArrow if on
                                       else Qt.RightArrow)

    def _on_labels_panel(self, on: bool):
        self.labels_panel.setVisible(bool(on))

    def _on_panel_pick(self, verdict):
        """A class clicked in the Labels panel: the keys are the grid's
        again, and the class is the active label - onto the picked
        chips, or the chip under the cursor, as the dropdown does."""
        self.setFocus()
        self.set_active(verdict)

    def _on_panel_reordered(self, names):
        """A class dragged into place in the Labels panel (2.9.8): the
        order is the user's from here, kept by name - other projects'
        names behind, where they were - and the keys follow it."""
        settings = app_settings()
        save_order(settings, list(names) + [name for name
                                            in load_order(settings)
                                            if name not in names])
        self._apply_order()
        self._show_active()
        self.setFocus()

    def _apply_order(self):
        """The window's order of the classes - the user's where they
        dragged one into place (class_order.py), the project's for the
        rest - into the dropdown and the panel; the keys go by it, the
        colours by the project's places."""
        self._classes = ordered(self._project_classes,
                                load_order(app_settings()))
        combo = self.label_combo
        combo.blockSignals(True)
        combo.clear()
        for index, name in enumerate(self._classes):
            combo.addItem(name)
            colour = self._colour_of(name)
            if colour is not None and self._colours:
                combo.setItemIcon(index, _swatch(colour))
        combo.blockSignals(False)
        self.labels_panel.set_classes(
            self._classes,
            [self._colour_by_name[name] for name in self._classes]
            if self._colours else [])

    def _on_details(self, on: bool):
        self.right.setVisible(bool(on))
        self.detail.setVisible(bool(on))
        self.reference.setVisible(bool(on))
        if on:
            self._request_detail()
            self._refresh_reference(force=True)

    # -- data in --------------------------------------------------------------

    def set_classes(self, names, colours=None):
        """The project's classes, in order, with their marker colours.

        A verdict naming a class the project no longer has is taken
        back: importing it would quietly put the class back."""
        self._project_classes = list(names)
        self._colours = list(colours or [])
        self._colour_by_name = {
            name: self._colours[i % len(self._colours)]
            for i, name in enumerate(self._project_classes)
        } if self._colours else {}
        self._apply_order()
        if self._active not in self._classes and self._active != IGNORE:
            self._active = self._classes[0] if self._classes else None
        self._show_active()
        orphaned = [chip for chip in self._chips
                    if chip.verdict not in (None, IGNORE)
                    and chip.verdict not in self._classes]
        for chip in orphaned:
            chip.verdict = None
        self._reference_dirty = True
        if orphaned:
            n = len(orphaned)
            self._note = (f"{n} verdict{'' if n == 1 else 's'} of a class "
                          "no longer in the project taken back.")
        self._refresh_cells()
        self._show_counts()

    def open_dialog(self):
        if not self._confirm_discard("Open another file anyway?"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open the model's mistakes",
            recent.last_dir(recent.REVIEW), "JSON (*.json);;All Files (*)")
        if path:
            recent.remember_dir(recent.REVIEW, path)
            self.open_file(path, ask=False)

    def pending(self) -> int:
        """Verdicts given and not yet imported."""
        return len(self.with_verdicts())

    def _confirm_discard(self, question: str) -> bool:
        """True when there is nothing to lose, or the user says so: a
        file opened over verdicts, or the window closed on them, took
        them silently before."""
        n = self.pending()
        if n == 0:
            return True
        answer = QMessageBox.question(
            self, "Model Review",
            f"{n} verdict{'' if n == 1 else 's'} not yet imported will be "
            f"lost. {question}", QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No)
        return answer == QMessageBox.Yes

    def open_file(self, path, ask: bool = True) -> bool:
        """Read and match a file of chips. False, having said why, when
        nothing in it could be shown - or when verdicts not yet imported
        would be lost and the user keeps them (``ask``)."""
        name = Path(str(path)).name
        if ask and not self._confirm_discard(f"Open {name} anyway?"):
            return False
        try:
            candidates, malformed = read_candidates(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._say(f"Could not read {name} as a file of chips.\n\n"
                      f"{type(exc).__name__}: {exc}")
            return False
        chips, report = match_candidates(candidates, self._project(),
                                         malformed,
                                         extra_classes=(CANDIDATE,))
        if not chips:
            self._say(f"Nothing in {name} could be matched to this "
                      f"project.\n\n{report.summary()}.")
            return False
        self._source, self._chips, self._report = name, chips, report
        self._path = str(path)
        self._write_failed = False
        self._misplaced_said = False
        self._stale = False
        self._by_key = {chip.key: chip for chip in chips}
        # A project of before 2.9.4 holds its own memory: its notes on
        # this file's chips go into the file, once, and count from now.
        moved = self._migrate_memory(chips, self._project())
        self._selected.clear()
        self._anchor = None
        self._cursor = None
        self._last_click = None
        self._undo.clear()
        self._redo.clear()
        self._page_start = 0
        self._set_detail(None)
        self._chips_gen += 1
        self._verdict_seq.clear()
        self._reference_dirty = True
        if self._image_window is not None:
            self._image_window.close()      # its chip was the old file's
        if self._working is not None:
            self.stop_work()                # its result would be the old file's
        self._suggestions.clear()
        self._learn_report = None
        self._fill_suggested_filter()
        self.setWindowTitle(f"Model Review - {name}")
        self.file_label.setText(f"{name}: {report.summary()}")
        if moved:
            written = self._write_outcomes(moved)
            n = len(moved)
            self._note = (f"{n} note{'' if n == 1 else 's'} moved from the "
                          f"project's memory into {name}"
                          + ("." if written else " - for this session only, "
                             "the file could not be written."))
        self.refresh_earlier()          # which also fills the grid
        self._show_learning()
        return True

    def _migrate_memory(self, chips, project) -> dict:
        """The project's old memory (formats 5.0 to 5.2) answering for
        chips the file has no note on: each takes the entry's note, the
        best matches first - the chip's group, then the class the model
        called it, then the nearest - and one entry answers for one
        chip (2.8.14). {chip key: the note} for the file."""
        memory = getattr(project, "model_review", None) or []
        if not memory:
            return {}
        index = LedgerIndex(memory, float(self.tolerance_spin.value()))
        pairs = []
        for chip in chips:
            if chip.earlier is not None:
                continue
            for goodness, entry in index.candidates(
                    chip.image_name, chip.source_x, chip.source_y,
                    chip.predicted, chip.group):
                pairs.append((goodness, chip.key, chip, entry))
        pairs.sort(key=lambda pair: (pair[0], pair[1]))
        moved: dict = {}
        taken: set = set()
        for _goodness, key, chip, entry in pairs:
            if key in moved or id(entry) in taken:
                continue
            taken.add(id(entry))
            chip.earlier = Verdict(
                image=chip.image_name, pixel_x=chip.source_x,
                pixel_y=chip.source_y, predicted=chip.predicted,
                score=float(chip.score), became=entry.became,
                label_id=entry.label_id, by=entry.by, at=entry.at,
                group=chip.group or "")
            moved[key] = chip.earlier
        return moved

    def file_path(self) -> str:
        """The file open, as a path; empty with none."""
        return self._path

    def _write_outcomes(self, notes: dict) -> bool:
        """``notes`` (chip key -> Verdict, or None to take one away)
        into the file; False, said once, when it cannot be written."""
        if not self._path or not notes:
            return False
        misplaced: list = []
        try:
            outcomes.write_outcomes(self._path, notes, misplaced)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if not self._write_failed:
                self._write_failed = True
                self._say(f"The verdicts could not be noted in "
                          f"{self._source}: {exc}\n\nThey hold for this "
                          "session; the file keeps no memory of them.")
            return False
        if misplaced and not self._misplaced_said:
            # The file is not the one opened: its records moved (2.9.6).
            self._misplaced_said = True
            n = len(misplaced)
            self._say(f"{n} note{'' if n == 1 else 's'} could not be placed "
                      f"in {self._source}: the file has changed since it was "
                      "opened, and those records are not the chips they "
                      "were. They hold for this session. Open the file "
                      "again to review it as it is now.")
        return not misplaced

    def record_outcomes(self, notes: dict) -> bool:
        """An import went through (main window): each chip takes its
        note - it is reviewed now, its verdict spent - and the file is
        written. The memory of what was done lives there (2.9.4)."""
        for key, verdict in notes.items():
            chip = self._by_key.get(key)
            if chip is None:
                continue
            chip.earlier = verdict
            chip.label_gone = False
            chip.verdict = None
            chip.as_new = False
        return self._write_outcomes(notes)

    def rename_verdicts(self, old: str, new: str):
        """The project renamed a class (2.9.2): the verdicts in hand
        that say ``old`` say ``new``, before set_classes would take them
        back as a class the project lost - and the file's notes that
        say it (2.9.5), written back."""
        renamed: dict = {}
        for chip in self._chips:
            if chip.verdict == old:
                chip.verdict = new
            if chip.earlier is not None and chip.earlier.became == old:
                chip.earlier.became = new
                renamed[chip.key] = chip.earlier
        if renamed:
            self._write_outcomes(renamed)
        settings = app_settings()
        order = load_order(settings)
        if old in order:
            save_order(settings, with_rename(order, old, new))

    def refresh_earlier(self, rebuild: bool = True):
        """The chips against the project as it stands (2.9.4): a note's
        label found, or gone; and the labels within the distance of
        each chip, which make it labelled already. Called whenever the
        project's labels change; with ``rebuild`` False the grid is
        rebuilt only when a chip's standing changed, else its cells are
        refreshed where they are."""
        project = self._project()
        images = getattr(project, "images", {}) or {}
        gone = [chip for chip in self._chips if chip.image_path not in images]
        if gone:
            # Their image left the project (a group removed, say): nothing
            # to label on, nothing to show.
            self._chips = [chip for chip in self._chips
                           if chip.image_path in images]
            self._by_key = {chip.key: chip for chip in self._chips}
            n = len(gone)
            self._note = (f"{n} chip{'' if n == 1 else 's'} whose image "
                          "left the project dropped.")
        before = [(chip.key, chip.reviewed_before(), chip.verdict)
                  for chip in self._chips]
        placed = {chip.key: (chip.pixel_x, chip.pixel_y)
                  for chip in self._chips}
        for chip in self._chips:
            self._seat(chip, project)
        moved = [chip for chip in self._chips
                 if (chip.pixel_x, chip.pixel_y) != placed[chip.key]]
        changed = labelled.mark_near(self._chips, project,
                                     float(self.labelled_spin.value()))
        after = [(chip.key, chip.reviewed_before(), chip.verdict)
                 for chip in self._chips]
        if self._suggestions:
            self._suggestions = {key: s for key, s in self._suggestions.items()
                                 if key in self._by_key}
            self._fill_suggested_filter()
        self._reference_dirty = True
        if (rebuild or gone or changed or before != after
                or [c.key for c in self._listed_chips()] != self.listed_keys()):
            # ...or the list would not be what the grid shows: a chip
            # cleared of its verdict since, labelled already, leaves it.
            self._fill_filter()
            self._rebuild()
        else:
            for chip in moved:
                # Seated where its label went (moved on the map): its
                # picture read again around it (2.9.6).
                self._reload(chip)
            self._refresh_cells()
            self._show_counts()

    def refresh_labels(self):
        """The project's labels changed: the grid follows, rebuilt only
        if a chip's standing did; a hidden window catches up when
        shown."""
        if self.isVisible():
            self.refresh_earlier(rebuild=False)
        else:
            self._stale = True

    def _seat(self, chip, project):
        """The chip against the file's note on it (2.9.4; the memory's
        answer before). A verdict that is what the chip became - imported
        a moment ago, or the same thing said again - is no verdict; one
        that differs stays, a class change to import (2.8.5). The chip
        is seated where its label is - the anchor a move is measured
        from - when the label is not there already. A note whose label
        is gone - removed by hand, an import undone - no longer stands:
        the labels are the truth, and the chip is plain again, measured
        from the file's centre."""
        earlier = chip.earlier
        if earlier is None:
            chip.label_gone = False
            return
        if earlier.became == IGNORE:
            chip.label_gone = False
            if chip.verdict == earlier.became:
                chip.verdict = None
            chip.unseat()
            return
        label = None
        find = getattr(project, "get_label_by_id", None)
        if earlier.label_id is not None and find is not None:
            image, label = find(earlier.label_id)
            if label is not None and self._image_of(chip) is not image:
                label = None        # on another image: not this chip's
            if label is not None and label.class_name != earlier.became:
                # Given another class since - by hand on the map, or an
                # import onto it undone: the note is stale, and the chip
                # is labelled already by that label (2.9.6).
                label = None
            if label is not None and math.hypot(
                    float(label.pixel_x) - chip.anchor_x,
                    float(label.pixel_y) - chip.anchor_y) \
                    > self.chip_spin.value():
                # Further than a chip's width from where the chip was
                # reviewed: not this chip's label, whatever its id says.
                # Ids used to be handed out again after an undo or Clear
                # All, and a note then latched onto a stranger - the chip
                # jumped onto it, and an Ignore deleted it (2.9.11).
                label = None
        if label is None:
            if not chip.label_gone:
                chip.label_gone = True
                chip.unseat()
            return
        if chip.label_gone:
            chip.label_gone = False     # back (an undo undone)
        if chip.verdict == earlier.became:
            chip.verdict = None
        # The anchor against where the label IS - not this entry against
        # the last: an undo in the project restores the memory as copies,
        # and by identity every reviewed chip was re-seated, its staged
        # move thrown away; by value, a move imported with no user name
        # set records an entry equal in every field (2.8.17).
        where = (float(label.pixel_x), float(label.pixel_y))
        if where == (chip.anchor_x, chip.anchor_y):
            return                  # nothing moved
        if chip.moved_since_import():
            chip.anchor_at(*where)      # a staged move stays staged
        else:
            chip.seat(*where)

    def note(self, text: str):
        """A word for the status line - what an import came to, say."""
        self._note = text
        self._show_counts()

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

    def cursor(self) -> "int | None":
        """The key of the keyboard's chip, or None."""
        return self._cursor

    def active(self) -> str | None:
        return self._active

    def with_verdicts(self) -> list:
        """The chips an import has something to do for (``pending``)."""
        return [chip for chip in self._chips if pending(chip)]

    def predicted_class(self):
        return self.class_filter.currentData()

    def set_predicted_class(self, name):
        index = self.class_filter.findData(name)
        if index >= 0:
            self.class_filter.setCurrentIndex(index)

    def set_show_reviewed(self, on: bool):
        self.show_reviewed_check.setChecked(bool(on))

    def set_show_labelled(self, on: bool):
        self.show_labelled_check.setChecked(bool(on))

    def _kept(self, chip) -> bool:
        """Is the chip one the grid lists at all: not reviewed before,
        nor labelled already, unless those are shown - but one with a
        verdict in hand, whatever its standing, is always listed: a
        second look given with Show reviewed on and then the box off
        left the Import button counting chips nobody could see (2.9.7).
        Not asked for the Show: list's standings (2.9.9), which list
        exactly those chips whatever the boxes say.
        """
        if pending(chip):
            return True
        if chip.reviewed_before() and not self.show_reviewed_check.isChecked():
            return False
        if chip.labelled_already() \
                and not self.show_labelled_check.isChecked():
            return False
        return True

    def _mark_near_one(self, chip):
        """The chip's centre moved: the labels within the distance of
        it again, at once, so a chip put onto a label is labelled
        already before any import."""
        own = chip.earlier.label_id if chip.reviewed_before() else None
        chip.near = labelled.near_labels(
            self._project(), chip.image_path, chip.pixel_x, chip.pixel_y,
            float(self.labelled_spin.value()), exclude=own)

    def _fill_filter(self):
        combo = self.class_filter
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        pool = [chip for chip in self._chips if self._kept(chip)]
        grouped = by_class(pool)
        combo.addItem(f"All classes ({len(pool)})", ALL_CLASSES)
        for name in sorted(grouped):
            combo.addItem(f"{name} ({len(grouped[name])})", name)
        index = combo.findData(current) if current is not None else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _fill_verdict_filter(self):
        """The Show: list's entries: the four; the standings with their
        counts, greyed at none (2.9.9); then one per class with chips
        labelled that class - this session's verdicts, and earlier
        rounds' when they are shown - the choice kept."""
        combo = self.verdict_filter
        show_reviewed = self.show_reviewed_check.isChecked()
        current = combo.currentData()
        standings = Counter()
        for chip in self._chips:
            if chip.reviewed_before():
                standings["reviewed"] += 1
                if chip.verdict is not None:
                    standings["changed"] += 1
            if chip.labelled_already():
                standings["already"] += 1
        # (text, data, enabled): the choice stays while it is the choice.
        wanted = [(f"{text} ({standings[data]})", data,
                   standings[data] > 0 or data == current)
                  for text, data in STANDING_FILTERS]
        counts: Counter = Counter()
        for chip in self._chips:
            if chip.reviewed_before() and not show_reviewed:
                standing = None
            elif chip.verdict is not None:
                standing = chip.verdict
            elif chip.reviewed_before():
                standing = chip.earlier.became
            else:
                standing = None
            if standing not in (None, IGNORE):
                counts[standing] += 1
        if (isinstance(current, str) and current.startswith("labelled:")
                and current[len("labelled:"):] not in counts):
            # The choice stays while it is the choice: the list settles
            # at a page turn, not under the cursor.
            counts[current[len("labelled:"):]] = 0
        classes = [(f"Labelled {name} ({counts[name]})", "labelled:" + name,
                    True) for name in sorted(counts)]
        have = [(combo.itemText(i), combo.itemData(i),
                 combo.model().item(i).isEnabled())
                for i in range(len(VERDICT_FILTERS), combo.count())
                if combo.itemData(i) is not None]
        if have == wanted + classes:
            return
        combo.blockSignals(True)
        while combo.count() > len(VERDICT_FILTERS):
            combo.removeItem(combo.count() - 1)
        for group in (wanted, classes):
            if group:
                combo.insertSeparator(combo.count())
            for text, data, enabled in group:
                combo.addItem(text, data)
                combo.model().item(combo.count() - 1).setEnabled(enabled)
        index = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _listed_chips(self) -> list:
        wanted = self.class_filter.currentData()
        standing = self.verdict_filter.currentData()
        suggested = (self.suggested_filter.currentData()
                     if self._suggestions else ANY_SUGGESTION)
        chosen = [chip for chip in self._chips
                  if (wanted is ALL_CLASSES or chip.predicted == wanted)
                  and (standing in STANDINGS or self._kept(chip))
                  and _passes(chip, standing)
                  and (suggested is ANY_SUGGESTION
                       or self._suggested_class(chip.key) == suggested)]
        # The score floor last, and counted: the status says how many
        # it hides (2.8.4). A hair under, for a floor typed as the
        # score was written.
        floor = self.score_spin.value() - 1e-9
        self._below_score = sum(1 for chip in chosen if chip.score < floor)
        chosen = [chip for chip in chosen if chip.score >= floor]
        if self._suggestions and self.order_combo.currentData() == "suggestion":
            return sorted(chosen, key=lambda c: (-self._confidence(c.key),
                                                 -c.score, c.key))
        return sorted(chosen, key=lambda c: (-c.score, c.key))

    def _step_page(self, delta: int) -> bool:
        """Turn ``delta`` pages on; True when the grid changed.

        The Show: list settles here: chips given verdicts since the
        page was built may have left it. The page turned to then
        begins at the chip that would have begun it, wherever that
        chip now stands in the list - so Next brings the chips after
        the ones on screen, however many of those left, and no chip
        is skipped or shown twice."""
        per_page = max(1, self.page_spin.value())
        old_keys = self.listed_keys()
        fresh_keys = [chip.key for chip in self._listed_chips()]
        has_next = self._page_start + per_page < len(old_keys)
        has_prev = self._page_start > 0
        if delta > 0 and has_next:
            want = self._page_start + per_page
        elif delta < 0 and has_prev:
            want = max(0, self._page_start - per_page)
        elif fresh_keys == old_keys:
            return False            # no page there, nothing changed
        else:
            want = self._page_start  # no page there: this one settles
        if fresh_keys != old_keys:
            place = {key: i for i, key in enumerate(fresh_keys)}
            landing = next((place[key] for key in old_keys[want:]
                            if key in place), None)
            want = (landing if landing is not None
                    else max(0, len(fresh_keys) - per_page))
        self._page_start = want
        self._rebuild()
        return True

    def _on_per_page(self, value):
        # Keep the place: the chip at the top of the page stays on it,
        # the page beginning at a multiple of the new size.
        per_page = max(1, int(value))
        self._page_start = (self._page_start // per_page) * per_page
        self._rebuild()

    def _on_show_reviewed(self, _on):
        self._page_start = 0
        self._fill_filter()             # its counts change with it
        self._rebuild()

    def _on_show_labelled(self, _on):
        self._page_start = 0
        self._fill_filter()
        self._rebuild()

    def _on_as_new(self, on: bool):
        """Details' tick: the chip in hand wants a new label beside the
        one already there, not its verdict onto it."""
        chip = self._by_key.get(self._detail_key)
        if chip is None or chip.as_new == bool(on):
            return
        chip.as_new = bool(on)
        self._refresh_cells()
        self._show_counts()

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
        self._frames.clear()
        self._listed = self._listed_chips()
        per_page = max(1, self.page_spin.value())
        total = len(self._listed)
        start = max(0, self._page_start)
        if start >= total:
            # The list shrank under the page: the last page of it.
            start = max(0, (total - 1) // per_page * per_page)
        self._page_start = start
        self._page = start // per_page
        self._visible = self._listed[start:start + per_page]
        keys = {chip.key for chip in self._visible}
        self._selected &= keys          # a selection lives on its page
        if self._anchor not in keys:
            self._anchor = None
        if self._cursor not in keys:
            self._cursor = None
        self.page_label.setText(
            f"{start + 1}-{start + len(self._visible)} of {len(self._listed)}"
            if self._listed else "none")
        self.prev_button.setEnabled(start > 0)
        self.next_button.setEnabled(start + len(self._visible) < total)
        columns = max(1, self.columns_spin.value())
        px = self._cell_px()
        for i, chip in enumerate(self._visible):
            cell = ChipCell(chip.key)
            cell.set_size(px)
            cell.set_mark(self.mark_check.isChecked())
            self._set_tooltip(cell, chip)
            cell.clicked.connect(self._on_cell_clicked)
            cell.cleared.connect(self._on_cell_cleared)
            cell.double_clicked.connect(self._on_cell_double_clicked)
            cell.recentred.connect(self._on_cell_recentred)
            cell.hovered.connect(self._on_cell_hovered)
            cell.left.connect(self._on_cell_left)
            self._grid.addWidget(cell, i // columns, i % columns)
            self._cells[chip.key] = cell
        if self._detail_key is not None \
                and self._detail_key not in self._by_key:
            self._set_detail(None)      # imported, or its image gone
        self._refresh_cells(reference=False)
        self._request_pixels()
        # cancel_all above dropped reads in flight for the panel and the
        # reference strip too.
        self._request_detail()
        self._refresh_reference(force=True)
        self._show_counts()

    def _set_tooltip(self, cell, chip):
        text = (f"{chip.image_name}  ({chip.pixel_x:.1f}, {chip.pixel_y:.1f})"
                f"\n{called(chip)}")
        suggestion = self._suggestions.get(chip.key)
        if suggestion is not None:
            text += "\nsuggested: " + suggestion.describe()
        if chip.rescaled:
            text += "\n(scaled from the size the file stated)"
        if chip.moved():
            text += (f"\ncentre moved {chip.offset():.0f} px from the "
                     f"file's ({chip.source_x:.1f}, {chip.source_y:.1f})")
        if chip.labelled_already():
            text += "\nalready in project: " + ", ".join(
                near.describe() for near in chip.near[:3])
            if chip.verdict is not None:
                text += ("\nas a new label beside it" if chip.as_new
                         else "\nthe verdict goes onto that label")
        if chip.reviewed_before():
            earlier = chip.earlier
            became = "Ignore" if earlier.became == IGNORE else earlier.became
            text += f"\nearlier: {became}"
            if earlier.by:
                text += f" by {earlier.by}"
            if earlier.at:
                text += f" on {earlier.at[:10]}"
            if earlier.source:
                text += f" ({earlier.source})"
            if chip.verdict is not None:
                text += "\nnow: " + ("Ignore" if chip.verdict == IGNORE
                                     else chip.verdict)
        cell.setToolTip(text)

    def _request_pixels(self):
        """The page's pictures, read grouped by image with the rows in
        view first (2.9.10): a page of hundreds asked for one by one
        held the window for its length and starved it while the reads
        ran; now the file is opened once per chunk, and what the user
        looks at comes first."""
        size = self.chip_spin.value()
        self.loader.request_many(
            [(chip.key, chip.image_path, chip.pixel_x, chip.pixel_y)
             for chip in self._fill_order()], size)

    def _fill_order(self) -> list:
        """The page's chips, the rows in view first and then outwards -
        by where the rows will lie, as the grid may not be laid out yet."""
        columns = max(1, self.columns_spin.value())
        pitch = self._cell_px() + CAPTION_PX + GRID_SPACING
        margin = self._grid.contentsMargins().top()
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()

        def away(i: int) -> int:
            y0 = margin + (i // columns) * pitch
            y1 = y0 + pitch
            if y1 < top:
                return top - y1
            if y0 > bottom:
                return y0 - bottom
            return 0
        return [chip for _i, chip in sorted(enumerate(self._visible),
                                            key=lambda p: (away(p[0]), p[0]))]

    def _refill_in_view(self):
        """After a scroll: the reads still queued for the page go on
        with the rows now in view first (2.9.10)."""
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()

        def away(key) -> int:
            cell = self._cells.get(key)
            if cell is None:
                return 10 ** 9
            box = cell.geometry()
            if box.bottom() < top:
                return top - box.bottom()
            if box.top() > bottom:
                return box.top() - bottom
            return 0
        self.loader.reprioritise(away)

    def _frame_of(self, chip, size: "int | None" = None) -> tuple:
        """(x0, y0, w, h): the source pixels a picture of ``size`` around
        the chip's centre shows - the window shifted to stay inside the
        image, exactly as the picture was read. The chip size in hand
        unless given."""
        if size is None:
            size = self.chip_spin.value()
        if chip.image_width and chip.image_height:
            return snippet_frame(chip.pixel_x, chip.pixel_y, size,
                                 chip.image_width, chip.image_height)
        return (int(round(chip.pixel_x)) - size // 2,
                int(round(chip.pixel_y)) - size // 2, size, size)

    def _on_pixels(self, key, arr):
        if isinstance(key, tuple):
            if key[0] == "detail":
                self._on_detail_pixels(key[1], arr)
            elif key[0] == "ref" and key[1] == self._ref_gen:
                self.reference.set_pixels(key[2], arr)
            return
        cell = self._cells.get(key)
        chip = self._by_key.get(key)
        if cell is None or chip is None or arr is None:
            return
        # The frame of THIS picture, from its own size: the chip size may
        # have changed since it was asked for. Reading the frame off the
        # size in hand put a double-click 80 px wrong on an old picture
        # (probed 2026-10-08).
        x0, y0, _w, _h = self._frame_of(chip, max(arr.shape[0], arr.shape[1]))
        self._pixels[key] = arr
        self._frames[key] = (x0, y0)
        cell.set_pixels(arr)
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
        if chip.key == self._detail_key:
            self._request_detail()
        self._refresh_image_window()

    def _on_chip_size(self, _value):
        # Reads in flight at the old size would land after the new ones
        # served from the cache, and show the old size.
        self.loader.cancel_all()
        self._request_pixels()
        self._request_detail()
        self._refresh_image_window()
        self._refresh_reference(force=True)

    # -- the centre: where the label will go -------------------------------

    def _on_cell_double_clicked(self, key):
        """The second press of a double-click: the verdict its first
        press gave is taken back, picture or no picture yet - a
        double-click is for the centre, not for labelling (users
        re-centring found chips labelled under them, 2026-10-07; on a
        chip still blank the verdict stayed, 2026-10-13)."""
        self.setFocus()
        chip = self._by_key.get(key)
        if chip is None or self._last_click is None \
                or self._last_click[0] != key:
            return
        _key, before, entry = self._last_click
        chip.verdict = before
        if entry is not None and self._undo and self._undo[-1] is entry:
            self._undo.pop()                # the half-click never happened
        self._last_click = None
        self._reference_dirty = True
        self._refresh_cells()
        self._show_counts()

    def _on_cell_recentred(self, key, col, row):
        """A double-click on the object: the label goes there. (The
        verdict the first press gave was taken back already, in
        _on_cell_double_clicked.)"""
        chip = self._by_key.get(key)
        cell = self._cells.get(key)
        # A chip reviewed before moves too (2.8.11): its label follows.
        if chip is None or cell is None or cell.frame is None:
            return
        # Through the frame of the picture clicked on, whatever the chip
        # size has become since it was read.
        x0, y0 = cell.frame
        self._remember([chip], "a centre move")
        chip.recenter(x0 + col, y0 + row)
        self._mark_near_one(chip)
        self._reload(chip)
        self._refresh_cells()
        self._note = f"Centre moved {chip.offset():.0f} px."
        self._show_counts()

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
            frame = self._frames.get(key)
            if chip is None or (chip.reviewed_before()
                                and not self._selected):
                continue        # a reviewed chip moves only when picked
            if arr is None or frame is None:
                waiting += 1
                continue
            x0, y0 = frame              # the frame of the picture held
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
            n = len(moves)
            self._remember([chip for chip, _x, _y in moves],
                           f"{n} centre move{'' if n == 1 else 's'}")
            for chip, new_x, new_y in moves:
                chip.recenter(new_x, new_y)
                self._mark_near_one(chip)
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

    # -- taking things back ---------------------------------------------------

    def _remember(self, chips, what: str) -> dict:
        """Before a change: these chips' verdicts and centres, for Ctrl+Z.
        ``what`` is how the status line will name it."""
        entry = {"what": what,
                 "chips": {chip.key: (chip.verdict, chip.pixel_x, chip.pixel_y)
                           for chip in chips}}
        self._undo.append(entry)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        return entry

    def _restore(self, entry: dict) -> dict:
        """Put these chips back as the entry holds them; returns the
        entry that undoes that, for the other stack."""
        replaced = {}
        for key, (verdict, x, y) in entry["chips"].items():
            chip = self._by_key.get(key)
            if chip is None:
                continue
            replaced[key] = (chip.verdict, chip.pixel_x, chip.pixel_y)
            if verdict not in (None, IGNORE) and verdict not in self._classes:
                verdict = None              # a class the project lost
            chip.verdict = verdict
            if (x, y) != (chip.pixel_x, chip.pixel_y):
                chip.recenter(x, y)
                self._mark_near_one(chip)
                if key in self._cells:
                    self._reload(chip)
                elif key == self._detail_key:
                    self._request_detail()
        self._reference_dirty = True
        self._refresh_cells()
        return {"what": entry["what"], "chips": replaced}

    def undo(self) -> bool:
        """Take the last thing done here back. False when there is
        nothing, and the main window's history is the thing to undo."""
        if not self._undo:
            return False
        entry = self._undo.pop()
        self._redo.append(self._restore(entry))
        self._last_click = None
        self._note = f"Taken back: {entry['what']}."
        self._show_counts()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        entry = self._redo.pop()
        self._undo.append(self._restore(entry))
        self._note = f"Done again: {entry['what']}."
        self._show_counts()
        return True

    # -- the grid's state -----------------------------------------------------

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
        colour = self._colour_by_name.get(class_name)
        if colour is not None:
            return colour
        return TEXT_COLOR if class_name else None

    def _refresh_cells(self, reference: bool = True):
        """Every cell's words and colours again; ``reference`` False
        leaves the strip to the caller (a rebuild refreshes it forced,
        and twice meant every picture read twice, 2.8.14)."""
        every_class = self.class_filter.currentData() is ALL_CLASSES
        for chip in self._visible:
            cell = self._cells.get(chip.key)
            if cell is None:
                continue
            if chip.reviewed_before() and chip.verdict is None:
                became = chip.earlier.became
                text = "earlier: " + ("Ignore" if became == IGNORE
                                      else became)
                if pending(chip):
                    text += " (moved)"
                colour = self._colour_of(became)
            elif chip.labelled_already() and chip.verdict is None:
                near = chip.nearest()
                text = f"in project: {near.class_name} {near.distance:.0f}px"
                colour = LABELLED_COLOR
            else:
                text = ("" if chip.verdict is None
                        else "Ignore" if chip.verdict == IGNORE
                        else chip.verdict)
                if chip.reviewed_before():
                    text += " (changed)"
                elif chip.labelled_already() and chip.verdict not in (
                        None, IGNORE):
                    text += " (new)" if chip.as_new else " (on label)"
                colour = (None if chip.verdict is None
                          else self._colour_of(chip.verdict))
            score = f"{chip.score:.2f}"
            if every_class:
                # Which of the model's calls this is, when they are mixed.
                score += f"  {chip.predicted}"
            suggestion = self._suggestions.get(chip.key)
            if suggestion is not None and chip.verdict is None \
                    and not chip.reviewed_before():
                # What the model learned here thinks, with a question
                # mark: the chip is still to look at.
                score = (f"{name_of(suggestion.class_name)}? "
                         f"{suggestion.confidence:.2f}")
            cell.set_state(score, text, colour, chip.key in self._selected,
                           chip.reviewed_before() and not pending(chip),
                           chip.key == self._cursor,
                           labelled=chip.labelled_already())
        self._refresh_detail_text()
        self._refresh_image_window()
        if reference:
            self._refresh_reference()
        self._fill_verdict_filter()

    def _show_counts(self):
        verdicts = self.with_verdicts()
        labelled = sum(1 for chip in verdicts
                       if effective_verdict(chip) != IGNORE)
        ignored = len(verdicts) - labelled
        n = len(verdicts)
        self.import_button.setEnabled(n > 0)
        self.import_button.setText(
            f"Import {n} to project" if n else "Import to project")
        self.labels_panel.set_counts(Counter(effective_verdict(chip)
                                             for chip in verdicts))
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
            text += (f"; {earlier} reviewed before"
                     + ("" if self.show_reviewed_check.isChecked()
                        else ", hidden"))
        already = sum(1 for chip in self._chips if chip.labelled_already())
        if already:
            text += (f"; {already} already in project"
                     + ("" if self.show_labelled_check.isChecked()
                        else ", hidden"))
        changed = sum(1 for chip in verdicts
                      if chip.reviewed_before() and chip.verdict is not None)
        if changed:
            text += f"; {changed} changed from an earlier round"
        moved = sum(1 for chip in verdicts if chip.verdict is None)
        if moved:
            text += f"; {moved} re-centred from an earlier round"
        if self._below_score:
            text += (f"; {self._below_score} below the score "
                     f"{self.score_spin.value():.2f}")
        if self._note:
            text += "  " + self._note
            self._note = ""
        self.status.setText(text)

    def _on_filter(self, _index):
        self._page_start = 0
        self._rebuild()

    # -- verdicts -------------------------------------------------------------

    def set_active(self, verdict):
        """The label a click gives: a class name, or IGNORE. Also given to
        the picked chips - or, with none picked, to the chip under the
        cursor, which then moves on."""
        if verdict != IGNORE and verdict not in self._classes:
            return
        self._active = verdict
        self._show_active()
        if self._selected:
            self._give(self._selected, verdict, None)
        elif self._cursor is not None:
            self._label_cursor(verdict)

    def _show_active(self):
        combo = self.label_combo
        combo.blockSignals(True)
        if self._active in self._classes:
            combo.setCurrentIndex(self._classes.index(self._active))
        combo.blockSignals(False)
        self.ignore_button.setChecked(self._active == IGNORE)
        self.labels_panel.set_active(self._active)

    def _give(self, keys, verdict, what: "str | None") -> "dict | None":
        """These chips get this verdict, as one thing to take back;
        returns the undo entry, or None when nothing changed. ``what``
        names it for the status line; None names it by its count."""
        # A chip reviewed in an earlier round takes a new verdict too
        # (2.8.5); given the class it already became, it has no change.
        chips, wanted = [], {}
        for key in keys:
            chip = self._by_key.get(key)
            if chip is None:
                continue
            new = verdict
            if chip.reviewed_before() and chip.earlier.became == verdict:
                new = None
            if chip.verdict != new:
                chips.append(chip)
                wanted[chip.key] = new
        if not chips:
            return None
        if what is None:
            n = len(chips)
            what = f"{n} verdict{'' if n == 1 else 's'}"
        entry = self._remember(chips, what)
        for chip in chips:
            chip.verdict = wanted[chip.key]
            self._verdict_seq[chip.key] = self._next_tick()
        self._reference_dirty = True
        self._refresh_cells()
        self._show_counts()
        return entry

    def _apply_active(self, keys) -> "dict | None":
        if not keys:
            return None
        if self._active is None:
            self._note = ("No class to give: the project has none. Add "
                          "class... makes one; 0 is Ignore.")
            self._show_counts()
            return None
        return self._give(keys, self._active, None)

    def clear_verdicts(self, keys):
        """Take the verdict back - and the centre, to where the
        project has it: its label's, else the file's (2.8.14)."""
        chips = [self._by_key[key] for key in list(keys)
                 if key in self._by_key
                 and (self._by_key[key].verdict is not None
                      or self._by_key[key].moved_since_import())]
        if not chips:
            return
        n = len(chips)
        self._remember(chips, f"{n} verdict{'' if n == 1 else 's'} cleared")
        for chip in chips:
            chip.verdict = None
            if chip.moved_since_import():
                chip.restore_centre()
                self._mark_near_one(chip)
                self._reload(chip)
        self._reference_dirty = True
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
        Ctrl-click builds a selection, for a key to label at once. A
        click is the mouse's: the keyboard's cursor steps aside."""
        self.setFocus()
        order = self.shown_keys()
        if key not in order:
            return
        # The cells are refreshed once, below or by the verdict given:
        # three refreshes a click was a third of a click's cost on a
        # page of hundreds (2.9.10).
        self.set_cursor(None, refresh=False)
        self._set_detail(key)
        if modifiers & Qt.ControlModifier:
            if key in self._selected:
                self._selected.discard(key)
            else:
                self._selected.add(key)
            self._anchor = key
            # Not a click that gave a verdict: a double-click after it
            # took back the verdict of a plain click before (2.8.14).
            self._last_click = None
            self._refresh_cells()
            return
        self._selected.clear()
        if modifiers & Qt.ShiftModifier and self._anchor in order:
            start, end = sorted((order.index(self._anchor), order.index(key)))
            run = order[start:end + 1]
            self._last_click = None
            self._anchor = key
            entry = self._apply_active(run)
        else:
            # Should this turn out to be the first half of a double-click,
            # the verdict it gives is taken back (_on_cell_recentred).
            before = self._by_key[key].verdict
            self._anchor = key
            entry = self._apply_active([key])
            self._last_click = (key, before, entry)
        if entry is None:
            self._refresh_cells()       # no verdict given: the cursor and
                                        # the selection still moved

    def _on_cell_cleared(self, key):
        self.setFocus()
        self._set_detail(key)
        self._last_click = None
        self.clear_verdicts([key])

    def select_all(self):
        self._selected = set(self.shown_keys())
        self._refresh_cells()

    def select_none(self):
        self._selected.clear()
        self.set_cursor(None)

    # -- the keyboard's cursor -----------------------------------------------

    def set_cursor(self, key: "int | None", refresh: bool = True):
        """Put the keyboard's cursor on this chip of the page, or on
        none, and bring it into view. ``refresh`` False leaves the
        cells to the caller, which refreshes them once (2.9.10)."""
        if key is not None and key not in self._cells:
            key = None
        self._cursor = key
        if key is not None:
            self._set_detail(key)
        if refresh:
            self._refresh_cells()
        if key is not None:
            self.scroll.ensureWidgetVisible(self._cells[key], 16, 16)

    def _cursor_start(self) -> int:
        """Where a cursor begins: after the chip last clicked, else at the
        first chip of the page."""
        order = self.shown_keys()
        if self._anchor in order:
            return min(order.index(self._anchor) + 1, len(order) - 1)
        return 0

    def _move_cursor(self, delta: int):
        """Step the cursor ``delta`` chips along the page's order; past
        either end the page turns and the cursor lands on the first or
        last chip of the next."""
        order = self.shown_keys()
        if not order:
            return
        if self._cursor not in order:
            index = self._cursor_start() if delta >= 0 else len(order) - 1
        else:
            index = order.index(self._cursor) + delta
        if index >= len(order):
            turned = self._step_page(1)
            keys = self.shown_keys()
            if turned and keys:
                self.set_cursor(keys[0])
            elif keys:
                self.set_cursor(keys[-1])
                self._note = "The last chip of the last page."
                self._show_counts()
            else:
                self.set_cursor(None)
                self._note = "Nothing left on this list."
                self._show_counts()
            return
        if index < 0:
            turned = self._page_start > 0 and self._step_page(-1)
            keys = self.shown_keys()
            if keys:
                self.set_cursor(keys[-1] if turned else keys[0])
            return
        self.set_cursor(order[index])

    def _label_cursor(self, verdict):
        """The chip under the cursor gets this verdict, and the cursor
        moves on."""
        if self._cursor is None:
            return
        self._give([self._cursor], verdict, None)
        self._move_cursor(1)

    def ignore_rest_of_page(self):
        """Every chip on the page without a verdict is ignored, as one
        thing to take back, and the next page comes up with the cursor on
        its first chip."""
        self.setFocus()
        rest = [chip.key for chip in self._visible
                if chip.verdict is None and not chip.reviewed_before()]
        n = len(rest)
        if rest:
            self._give(rest, IGNORE, f"the rest of the page ignored ({n})")
        turned = self._step_page(1)
        keys = self.shown_keys()
        self.set_cursor(keys[0] if turned and keys else None)
        self._note = (f"{n} chip{'' if n == 1 else 's'} ignored"
                      + (", next page" if turned and keys else "") + ".")
        self._show_counts()

    # -- the detail panel: the chip in hand -----------------------------------

    def _on_cell_hovered(self, key):
        self._hover_key = key
        self._hover_timer.start()

    def _on_cell_left(self, key):
        if self._hover_key == key:
            self._hover_timer.stop()
            self._hover_key = None

    def _on_hover_rest(self):
        """The mouse has rested on a chip: the panel turns to it."""
        if self._hover_key in self._cells:
            self._set_detail(self._hover_key)

    def detail_key(self) -> "int | None":
        """The key of the chip in hand - the panel's - or None."""
        return self._detail_key

    def _set_detail(self, key):
        """This chip is in hand: the panel shows it, and reads its
        surroundings if it is new to it."""
        chip = self._by_key.get(key) if key is not None else None
        if chip is None:
            self._detail_key = None
            self.detail.clear()
            return
        changed = key != self._detail_key
        self._detail_key = key
        self.detail.show_chip(chip, self._latlon_of(chip),
                              self._suggestions.get(chip.key),
                              image=self._image_of(chip), near=chip.near)
        if changed:
            self._request_detail()

    def _image_of(self, chip):
        """The project's entry for the chip's image, or None."""
        images = getattr(self._project(), "images", {}) or {}
        return images.get(chip.image_path)

    def _latlon_of(self, chip) -> "tuple | None":
        image = self._image_of(chip)
        if image is None:
            return None
        return image.pixel_to_latlon(chip.pixel_x, chip.pixel_y)

    def _refresh_detail_text(self):
        chip = self._by_key.get(self._detail_key)
        if chip is not None:
            self.detail.show_chip(chip, self._latlon_of(chip),
                                  self._suggestions.get(chip.key),
                                  image=self._image_of(chip), near=chip.near)

    def _request_detail(self):
        """The surroundings of the chip in hand, read at the context
        factor times the chip size - unless the panel is hidden, when
        nothing is read for it."""
        chip = self._by_key.get(self._detail_key)
        if chip is None or not self.details_check.isChecked():
            return
        self.loader.request(("detail", chip.key), chip.image_path,
                            chip.pixel_x, chip.pixel_y,
                            self.chip_spin.value() * self.detail.factor())

    def _on_detail_pixels(self, key, arr):
        chip = self._by_key.get(key)
        if chip is None or key != self._detail_key:
            return
        if arr is None:
            self.detail.picture_failed()    # not "Reading..." for good
            return
        # The picture's own frame, from its size (see _on_pixels).
        px0, py0, _w, _h = self._frame_of(chip, max(arr.shape[0],
                                                    arr.shape[1]))
        self.detail.set_picture(chip, arr, (px0, py0), self._frame_of(chip))

    def _refresh_image_window(self):
        """The image window, if up, follows its chip's verdict, centre
        and the chip size - without coming forward."""
        shown = self._image_window
        if shown is None or not shown.isVisible():
            return
        chip = shown.chip()
        if chip is not None and self._by_key.get(chip.key) is chip:
            shown.refresh(self.chip_spin.value(), standing_of(chip))

    def show_on_image(self):
        """The chip in hand on the whole of its image, in a window of
        its own (image_window.py). M."""
        self.setFocus()
        chip = self._by_key.get(self._detail_key)
        if chip is None:
            self._note = ("No chip in hand to show: move the cursor onto "
                          "one, or click one.")
            self._show_counts()
            return
        if self._image_window is None:
            self._image_window = ImageWindow(self)
        self._image_window.show_chip(chip, self.chip_spin.value(),
                                     standing_of(chip))

    # -- the reference strip: chips already given a class ------------------

    def _next_tick(self) -> int:
        self._tick += 1
        return self._tick

    def _refresh_reference(self, force: bool = False):
        """The strip follows the verdicts: the classes with examples,
        and the chosen class's chips, the newest verdict first, then
        earlier rounds, then the project's labels. ``force`` reads
        the pictures again (the chip size changed)."""
        if not self.details_check.isChecked():
            return
        if not force and not self._reference_dirty:
            return                      # a cursor step gathers nothing
        self._reference_dirty = False
        examples = gather_examples(self._project(), self._chips)
        self.reference.set_classes(
            Counter(e.class_name for e in examples))
        chosen = self.reference.chosen()
        when = {(chip.image_path, int(round(chip.pixel_x)),
                 int(round(chip.pixel_y))): self._verdict_seq.get(chip.key, 0)
                for chip in self._chips if chip.verdict is not None}
        rank = {"verdict": 0, "memory": 1, "label": 2}
        wanted = sorted(
            (e for e in examples if e.class_name == chosen),
            key=lambda e: (rank.get(e.origin, 3), -when.get(
                (e.image_path, int(round(e.pixel_x)),
                 int(round(e.pixel_y))), 0)))
        shown = wanted[:MAX_SHOWN]
        text = ""
        if chosen is not None and wanted:
            n = len(wanted)
            parts = []
            here = sum(1 for e in wanted if e.origin == "verdict")
            earlier = sum(1 for e in wanted if e.origin == "memory")
            labels = sum(1 for e in wanted if e.origin == "label")
            if here:
                parts.append(f"{here} verdict{'' if here == 1 else 's'} here")
            if earlier:
                parts.append(f"{earlier} from earlier rounds")
            if labels:
                parts.append(f"{labels} label{'' if labels == 1 else 's'}")
            text = (f"{n} {name_of(chosen)}"
                    + (f", the first {len(shown)} shown" if n > len(shown)
                       else "")
                    + " - " + ", ".join(parts) + ".")
        if self.reference.set_examples(shown, text) or force:
            self._ref_gen += 1
            for i, e in enumerate(shown):
                self.loader.request(("ref", self._ref_gen, i),
                                    e.image_path, e.pixel_x, e.pixel_y,
                                    self.chip_spin.value())

    # -- PyTorch, looked for without waiting ---------------------------------

    def _start_probe(self):
        """What PyTorch there is, found on a thread unless known
        already: the import takes seconds, and the window need not."""
        known = learning_checked()
        if known is not None:
            self._learning = known
            self._show_learning()
            return
        probe = LearnThread(lambda _progress, _stop: learning_available(),
                            self)
        probe.done.connect(self._on_probed)
        probe.finished.connect(self._on_probe_finished)
        probe.finished.connect(probe.deleteLater)
        self._probe = probe
        probe.start()

    def _on_probed(self, result):
        self._learning = tuple(result)
        self._show_learning()

    def _on_probe_finished(self):
        self._probe = None

    # -- learning: the verdicts taught to a model, to sort what is left --

    def _suggestion(self, key):
        return self._suggestions.get(key)

    def _suggested_class(self, key):
        suggestion = self._suggestions.get(key)
        return None if suggestion is None else suggestion.class_name

    def _confidence(self, key) -> float:
        suggestion = self._suggestions.get(key)
        return -1.0 if suggestion is None else suggestion.confidence

    def suggestions(self) -> dict:
        """key -> Suggestion, for the chips the model suggested for."""
        return dict(self._suggestions)

    def _fill_suggested_filter(self):
        combo = self.suggested_filter
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        counts: dict = {}
        for suggestion in self._suggestions.values():
            counts[suggestion.class_name] = counts.get(
                suggestion.class_name, 0) + 1
        combo.addItem(f"Any ({len(self._suggestions)})", ANY_SUGGESTION)
        for name in sorted(counts):
            combo.addItem(f"{name_of(name)} ({counts[name]})", name)
        index = combo.findData(current) if current is not None else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _show_learning(self):
        """The Learn row's state: what can be done, and what came of
        the last training."""
        device, text = self._learning
        training = self._working is not None
        have_chips = bool(self._chips)
        self.train_button.setEnabled(device is not None and not training
                                     and have_chips)
        self.suggest_button.setEnabled(
            device is not None and not training and have_chips
            and self._learner is not None and self._learner.trained())
        self.save_button.setEnabled(not training and have_chips)
        self.clear_button.setEnabled(not training)
        self.backbone_combo.setEnabled(not training)
        self.results_button.setEnabled(self._learn_window is not None)
        self.stop_button.setVisible(training)
        have = bool(self._suggestions)
        for widget in (self.order_combo, self.suggested_filter,
                       self.accept_button, self.accept_page_button,
                       self.accept_spin):
            widget.setEnabled(have)
        if training:
            self.learn_label.setText(self._learn_progress)
        elif self._learn_report is not None:
            self.learn_label.setText(self._learn_report.summary())
        else:
            self.learn_label.setText(text)

    def _places_to_look_at(self) -> list:
        """(key, path, x, y) for every chip still without a verdict."""
        return [(chip.key, chip.image_path, chip.pixel_x, chip.pixel_y)
                for chip in self._chips
                if chip.verdict is None and not chip.reviewed_before()]

    def _learning_device(self) -> "str | None":
        """The device, or None having said why there is none."""
        device, text = self._learning
        if device is None:
            self._note = text
            self._show_counts()
        return device

    def train(self):
        """Train on the verdicts and suggest for what is left, off the
        GUI thread (learner.py, training.py)."""
        self.setFocus()
        if self._working is not None:
            return
        device = self._learning_device()
        if device is None:
            return
        backbone = self.backbone_combo.currentData()
        why = backbone_reason(backbone)
        if why:
            self._note = why + "."
            self._show_counts()
            return
        examples = gather_examples(self._project(), self._chips)
        why_not = readiness(examples)
        if why_not:
            self._note = why_not
            self._show_counts()
            return
        size = self.chip_spin.value()
        if (self._learner is None or self._learner.chip_size != size
                or self._learner.backbone != backbone):
            self._learner = Learner(size, device, backbone=backbone,
                                    store=self._store)
        places = self._places_to_look_at()
        self._start_work("train", f"Training on {len(examples)} chips, "
                         f"for {len(places)} to look at",
                         train_job(self._learner, examples, places))

    def suggest(self):
        """Suggestions again from the model as trained."""
        self.setFocus()
        if self._working is not None or self._learning_device() is None:
            return
        if self._learner is None or not self._learner.trained():
            self._note = "Train first."
            self._show_counts()
            return
        places = self._places_to_look_at()
        self._start_work("suggest", f"Suggesting for {len(places)} chips",
                         suggest_job(self._learner, places))

    def save_snippets(self):
        """Every chip of the file, and everything there is to learn
        from, into the snippet store ahead of time."""
        self.setFocus()
        if self._working is not None:
            return
        places = [(chip.image_path, chip.pixel_x, chip.pixel_y)
                  for chip in self._chips]
        places += [(e.image_path, e.pixel_x, e.pixel_y)
                   for e in gather_examples(self._project(), self._chips)]
        self._start_work("save", f"Saving {len(places)} snippets",
                         save_job(self._store, places,
                                  self.chip_spin.value()))

    def clear_snippets(self):
        self.setFocus()
        n = self._store.clear()
        if self._learner is not None:
            self._learner._pixels.clear()
        self._note = (f"{n} snippet file{'' if n == 1 else 's'} deleted "
                      f"from {self._store.root}.")
        self._show_counts()

    def show_results(self):
        window = self._results_window()
        window.show()
        window.raise_()

    def _results_window(self) -> LearnWindow:
        if self._learn_window is None:
            self._learn_window = LearnWindow(self)
            self._learn_window.stop_requested.connect(self.stop_work)
            self.accept_spin.valueChanged.connect(
                self._learn_window.set_threshold)
            self._learn_window.set_suggestions(self._suggestions,
                                               self.accept_spin.value())
            if self._learn_report is not None:
                self._learn_window.set_report(self._learn_report)
        return self._learn_window

    def _start_work(self, kind: str, title: str, job):
        """Run the job on a thread of its own, the training window
        showing it; a method of its own so tests can stand in for it."""
        self._work_kind = kind
        self._work_gen = self._chips_gen
        self._learn_progress = title + "..."
        thread = LearnThread(job, self)
        thread.progress.connect(self._on_work_progress)
        thread.done.connect(self._on_work_done)
        thread.failed.connect(self._on_work_failed)
        thread.finished.connect(self._on_work_finished)
        self._working = thread
        window = self._results_window()
        window.start(title)
        window.show()
        window.raise_()
        thread.start()
        self._show_learning()

    def stop_work(self):
        if self._working is not None:
            self._working.stop()
            self._learn_progress = "Stopping..."
            self._results_window().stopping()
            self._show_learning()

    def _on_work_progress(self, text, done, total):
        self._learn_progress = text
        self.learn_label.setText(text)
        self._results_window().progress(text, done, total)

    def _take_suggestions(self, suggestions: dict):
        """The model's suggestions are in: the grid goes over to them,
        the surest first."""
        self._suggestions = {key: s for key, s in suggestions.items()
                             if key in self._by_key}
        self._fill_suggested_filter()
        combo = self.order_combo
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, combo.findData("suggestion")))
        combo.blockSignals(False)
        self._page_start = 0
        self._rebuild()

    def _on_work_done(self, result):
        kind = self._work_kind
        text = None
        if self._work_gen != self._chips_gen:
            # Begun for the file before this one: its suggestions would
            # land on this file's chips by number.
            text = ("Finished, for the file before this one; dropped. "
                    "Train again for this one.")
            self._note = text
            self._show_counts()
            self._results_window().finished(None, self._suggestions,
                                            self.accept_spin.value(), text)
            return
        if kind == "train":
            report, suggestions = result
            self._learn_report = report
            self._take_suggestions(suggestions)
            n = len(suggestions)
            accuracy = (f"held-out accuracy {report.accuracy:.0%}; "
                        if report.held_out else "")
            self._note = (f"Trained: {accuracy}{n} chip"
                          f"{'' if n == 1 else 's'} given a suggestion, "
                          "the grid ordered by it.")
        elif kind == "suggest":
            self._take_suggestions(result)
            n = len(result)
            self._note = (f"{n} chip{'' if n == 1 else 's'} given a "
                          "suggestion, the grid ordered by it.")
        else:
            text = f"{result} snippets read; " + self._store.describe()
            self._note = text
        self._show_counts()
        self._results_window().finished(
            self._learn_report if kind == "train" else None,
            self._suggestions, self.accept_spin.value(), text)

    def _on_work_failed(self, text):
        self._learn_progress = ""
        self._note = text
        self._show_counts()
        self._results_window().failed(text)

    def _on_work_finished(self):
        thread, self._working = self._working, None
        if thread is not None:
            thread.deleteLater()
        self._show_learning()

    def _suggested_pairs(self, keys) -> list:
        """(chip, suggested class) for these keys, where there is a
        suggestion and no verdict yet."""
        pairs = []
        for key in keys:
            chip = self._by_key.get(key)
            suggestion = self._suggestions.get(key)
            if (chip is not None and suggestion is not None
                    and chip.verdict is None and not chip.reviewed_before()):
                pairs.append((chip, suggestion.class_name))
        return pairs

    def _give_each(self, pairs, what: str) -> "dict | None":
        """Each chip its own verdict, as one thing to take back."""
        chips = [chip for chip, verdict in pairs
                 if not chip.reviewed_before() and chip.verdict != verdict]
        if not chips:
            return None
        entry = self._remember(chips, what)
        wanted = {chip.key: verdict for chip, verdict in pairs}
        for chip in chips:
            chip.verdict = wanted[chip.key]
            self._verdict_seq[chip.key] = self._next_tick()
        self._reference_dirty = True
        self._refresh_cells()
        self._show_counts()
        return entry

    def accept_suggestions(self):
        """The suggested class onto the picked chips - or onto the chip
        under the cursor, which then moves on. S."""
        self.setFocus()
        if self._selected:
            pairs = self._suggested_pairs(sorted(self._selected))
            n = len(pairs)
            if self._give_each(pairs, f"{n} suggestion"
                               f"{'' if n == 1 else 's'} accepted") is None:
                self._note = "No suggestion to accept on the picked chips."
                self._show_counts()
        elif self._cursor is not None:
            pairs = self._suggested_pairs([self._cursor])
            if pairs:
                self._give_each(pairs, "a suggestion accepted")
            self._move_cursor(1)
        else:
            self._note = ("Pick chips, or put the cursor on one, to accept "
                          "the suggestion.")
            self._show_counts()

    def accept_page(self):
        """Every chip on the page without a verdict whose suggestion is
        at least as sure as the box says gets it, as one thing to take
        back."""
        self.setFocus()
        floor = self.accept_spin.value()
        pairs = [(chip, suggestion.class_name) for chip in self._visible
                 for suggestion in [self._suggestions.get(chip.key)]
                 if suggestion is not None and chip.verdict is None
                 and not chip.reviewed_before()
                 and suggestion.confidence >= floor]
        n = len(pairs)
        if pairs:
            self._give_each(pairs, f"the page's suggestions accepted ({n})")
        self._note = (f"{n} suggestion{'' if n == 1 else 's'} at or above "
                      f"{floor:.2f} accepted on this page.")
        self._show_counts()

    # -- import ---------------------------------------------------------------

    def _import_summary(self, chips) -> str:
        """What an import would do, for the question that precedes it."""
        counts: dict = {}
        ignored = moved = 0
        for chip in chips:
            verdict = effective_verdict(chip)
            if verdict == IGNORE:
                ignored += 1
            else:
                counts[verdict] = counts.get(verdict, 0) + 1
            if chip.moved_since_import():
                moved += 1
        parts = [f"{n} {name}" for name, n in
                 sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
        if ignored:
            parts.append(f"{ignored} ignored")
        text = (f"Import {len(chips)} chip{'' if len(chips) == 1 else 's'} "
                f"from {self._source}?\n\n" + ", ".join(parts) + ".")
        if moved:
            text += (f"\n{moved} re-centred: the label sits where the "
                     "centre was put.")
        changed = sum(1 for chip in chips
                      if chip.reviewed_before() and chip.verdict is not None)
        if changed:
            text += (f"\n{changed} reviewed before: the label changes "
                     "class, goes for Ignore, or is made for a chip "
                     "ignored before.")
        onto = sum(1 for chip in chips if chip.labelled_already()
                   and chip.verdict not in (None, IGNORE) and not chip.as_new)
        beside = sum(1 for chip in chips if chip.labelled_already()
                     and chip.verdict not in (None, IGNORE) and chip.as_new)
        if onto:
            text += (f"\n{onto} already in project: the verdict goes onto "
                     "the label there, which takes the class.")
        if beside:
            text += (f"\n{beside} already in project: a new label beside "
                     "the one there.")
        return text

    def _confirm_import(self, text: str) -> bool:
        """The question before an import; a method of its own so tests
        can answer it."""
        answer = QMessageBox.question(
            self, "Import to project", text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes)
        return answer == QMessageBox.Yes

    def _import(self):
        chips = self.with_verdicts()
        if not chips:
            return
        if not self._confirm_import(self._import_summary(chips)):
            return
        # The verdicts and centres go into the project with the labels;
        # from here Ctrl+Z is the project's history.
        self._undo.clear()
        self._redo.clear()
        self._last_click = None
        # The anchors follow in refresh_earlier, once the project has
        # the labels: settled here, before the import ran, they were
        # wrong whenever it did not (2.8.14).
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
                # What was done here first; then the project's history.
                if not self.undo():
                    self.undo_requested.emit()
            elif key == Qt.Key_Y:
                if not self.redo():
                    self.redo_requested.emit()
            elif key == Qt.Key_A:
                self.select_all()
            elif key in (Qt.Key_Return, Qt.Key_Enter):
                self._import()
            else:
                super().keyPressEvent(event)
            return
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) \
                and key == Qt.Key_Z:
            if not self.redo():
                self.redo_requested.emit()
            return
        if mods == Qt.ShiftModifier:
            # The tenth to the eighteenth class (2.9.8); the shifted
            # symbol or the digit, whichever the layout reports.
            digit = SHIFTED_DIGITS.get(key)
            if digit:
                index = 8 + digit
                if index < len(self._classes):
                    self.set_active(self._classes[index])
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
                if self._selected:
                    self.clear_verdicts(self._selected)
                elif self._cursor is not None:
                    self.clear_verdicts([self._cursor])
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
            if key == Qt.Key_Right:
                self._move_cursor(1)
                return
            if key == Qt.Key_Left:
                self._move_cursor(-1)
                return
            if key == Qt.Key_Down:
                self._move_cursor(max(1, self.columns_spin.value()))
                return
            if key == Qt.Key_Up:
                self._move_cursor(-max(1, self.columns_spin.value()))
                return
            if key == Qt.Key_Home:
                if self.shown_keys():
                    self.set_cursor(self.shown_keys()[0])
                return
            if key == Qt.Key_End:
                if self.shown_keys():
                    self.set_cursor(self.shown_keys()[-1])
                return
            if key == Qt.Key_Space:
                # On to the next chip, this one left as it is.
                self._move_cursor(1)
                return
            if key in (Qt.Key_Return, Qt.Key_Enter):
                # The active label onto the cursor's chip, and on - or,
                # with no cursor yet, the cursor's first step.
                if self._cursor is None:
                    self._move_cursor(1)
                elif self._active is not None:
                    self._label_cursor(self._active)
                else:
                    self._apply_active([self._cursor])
                return
            if key == Qt.Key_I:
                self.ignore_rest_of_page()
                return
            if key == Qt.Key_M:
                self.show_on_image()
                return
            if key == Qt.Key_R:
                self.detail.measure_button.toggle()
                return
            if key == Qt.Key_S:
                self.accept_suggestions()
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
                 int(DEFAULT_TOLERANCE_PX)),
                (self.labelled_spin, _LABELLED_KEY,
                 int(round(labelled.default_distance())))):
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
        self.show_labelled_check.blockSignals(True)
        # On to start with (2.9.7): the users asked for these chips to
        # be told apart, not taken away.
        self.show_labelled_check.setChecked(
            str(settings.value(_SHOW_LABELLED_KEY, "true")).lower()
            != "false")
        self.show_labelled_check.blockSignals(False)
        self.labels_check.setChecked(          # off: toggled hides the panel
            str(settings.value(_LABELS_KEY, "true")).lower() != "false")
        self.mark_check.setChecked(
            str(settings.value(_MARK_KEY, "true")).lower() != "false")
        self.more_button.setChecked(
            str(settings.value(_MORE_KEY, "false")).lower() == "true")
        self.learn_button.setChecked(
            str(settings.value(_LEARN_KEY, "false")).lower() == "true")
        try:
            columns = int(settings.value(_REF_COLUMNS_KEY,
                                         DEFAULT_REF_COLUMNS))
        except (TypeError, ValueError):
            columns = DEFAULT_REF_COLUMNS
        self.reference.set_columns(columns)
        try:
            floor = float(settings.value(_MIN_SCORE_KEY, 0.0))
        except (TypeError, ValueError):
            floor = 0.0
        self.score_spin.blockSignals(True)
        self.score_spin.setValue(floor)
        self.score_spin.blockSignals(False)
        self.details_check.setChecked(
            str(settings.value(_DETAILS_KEY, "true")).lower() != "false")
        self.detail.setVisible(self.details_check.isChecked())
        try:
            context = int(settings.value(_CONTEXT_KEY, DEFAULT_CONTEXT))
        except (TypeError, ValueError):
            context = DEFAULT_CONTEXT
        self.detail.context_spin.blockSignals(True)
        self.detail.context_spin.setValue(context)
        self.detail.context_spin.blockSignals(False)
        try:
            accept = float(settings.value(_ACCEPT_KEY, DEFAULT_ACCEPT))
        except (TypeError, ValueError):
            accept = DEFAULT_ACCEPT
        self.accept_spin.setValue(accept)
        index = self.backbone_combo.findData(
            str(settings.value(_BACKBONE_KEY, "")))
        if index >= 0 and self.backbone_combo.model().item(index).isEnabled():
            self.backbone_combo.setCurrentIndex(index)
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
        for widget, key in ((self.splitter, _SPLITTER_KEY),
                            (self.right, _RIGHT_KEY)):
            state = settings.value(key)
            if state is not None:
                try:
                    widget.restoreState(state)
                except TypeError:
                    pass

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
        settings.setValue(_LABELLED_KEY, self.labelled_spin.value())
        settings.setValue(_SHOW_LABELLED_KEY,
                          "true" if self.show_labelled_check.isChecked()
                          else "false")
        settings.setValue(_SHOW_REVIEWED_KEY,
                          self.show_reviewed_check.isChecked())
        settings.setValue(_MARK_KEY, self.mark_check.isChecked())
        settings.setValue(_MORE_KEY, self.more_button.isChecked())
        settings.setValue(_LEARN_KEY, self.learn_button.isChecked())
        settings.setValue(_REF_COLUMNS_KEY, self.reference.columns)
        settings.setValue(_MIN_SCORE_KEY, self.score_spin.value())
        settings.setValue(_DETAILS_KEY, self.details_check.isChecked())
        settings.setValue(_LABELS_KEY, self.labels_check.isChecked())
        settings.setValue(_CONTEXT_KEY, self.detail.context_spin.value())
        settings.setValue(_SPLITTER_KEY, self.splitter.saveState())
        settings.remove(_OLD_SPLITTER_KEY)
        settings.setValue(_RIGHT_KEY, self.right.saveState())
        settings.setValue(_ACCEPT_KEY, self.accept_spin.value())
        settings.setValue(_BACKBONE_KEY, self.backbone_combo.currentData())
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())

    def discard_and_close(self):
        """Close without asking: the project is being replaced, and the
        user has already answered for its unsaved work."""
        self._discard_on_close = True
        self.close()

    def closeEvent(self, event):
        if not self._discard_on_close and not self._confirm_discard(
                "Close the window anyway?"):
            event.ignore()
            return
        self.save_settings()
        self.loader.shutdown()
        self._hover_timer.stop()
        if self._image_window is not None:
            self._image_window.close()
        if self._working is not None:
            self._working.stop()
            if not self._working.wait(WORK_WAIT_MS):
                # Still reading or training: a QThread destroyed while
                # running aborts the whole process. It goes on alone,
                # its result unheard, and deletes itself when done.
                worker = self._working
                for signal, slot in ((worker.progress, self._on_work_progress),
                                     (worker.done, self._on_work_done),
                                     (worker.failed, self._on_work_failed),
                                     (worker.finished, self._on_work_finished)):
                    signal.disconnect(slot)
                worker.setParent(None)
                worker.finished.connect(worker.deleteLater)
                self._working = None
                # Its model is still being trained on: the next Train
                # starts a learner of its own, and the row says the
                # window is free again (2.8.14).
                self._learner = None
                self._learn_progress = ""
                self._show_learning()
        if self._learn_window is not None:
            self._learn_window.close()
        if self._probe is not None and self._probe.isRunning():
            # An import cannot be stopped: the thread goes on alone and
            # deletes itself when done.
            self._probe.setParent(None)
            self._probe = None
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._stale:
            # The labels changed while the window was hidden (2.9.5).
            self._stale = False
            self.refresh_earlier()
            return                      # which asked for every picture
        # Closed while its pictures were still coming, the window
        # cancelled them (closeEvent); shown again, nothing asked for
        # them, and the cells stayed blank (2.8.14).
        if self._chips:
            self._request_unread()

    def _request_unread(self):
        """The pictures the window lacks, asked for again."""
        size = self.chip_spin.value()
        for chip in self._visible:
            if chip.key not in self._pixels:
                self.loader.request(chip.key, chip.image_path,
                                    chip.pixel_x, chip.pixel_y, size)
        if self._detail_key is not None and not self.detail.has_picture():
            self._request_detail()
        if self.reference.pictures() < len(self.reference.examples()):
            self._refresh_reference(force=True)

    def _say(self, text: str):
        QMessageBox.information(self, "Model Review", text)
