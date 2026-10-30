"""The detail panel: the chip in hand, larger and with its surroundings.

Asked for 2026-10-13. A doubt about a small square usually dissolves
with more context: the panel shows the chip under the cursor - or the
one last clicked, or the one the mouse rests on - read again at several
times the chip size, with a box where the chip itself is and the mark
where the label will go, and under it everything known: image,
position, latitude and longitude, the model's call and score, the
verdict, a moved centre, an earlier round's verdict. Show on image (M)
opens the whole image (image_window.py).

The panel draws what it is given; the window reads the pixels and
decides which chip is in hand.
"""
from __future__ import annotations

import html
import math

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSpinBox,
                             QVBoxLayout, QWidget)

from ..labels import geodesic_distance
from .ledger import IGNORE
from .source import called

DEFAULT_CONTEXT = 3             # the picture is this many chips across
MIN_CONTEXT, MAX_CONTEXT = 2, 8
BOX_COLOR = QColor(255, 200, 0)
MARK_COLOR = QColor(255, 255, 255, 230)
NEAR_COLOR = QColor(80, 200, 255)       # a label already there (2.9.4)
NEAR_RING_PX = 8
MOVED_MARK_COLOR = QColor(0, 220, 255, 240)
MARK_RING_PX = 6
MARK_GAP_PX = 9
MARK_PX = 9
NOTHING = "No chip in hand: move the cursor onto one, or click one."
DESCRIPTION_LINES = 4           # the Details box shows this many, scrolling
COULD_NOT_READ = "No picture: this chip could not be read."


def standing_of(chip) -> str:
    """What the chip is, in a few words: its verdict, what it became
    before, that it is labelled already, or nothing yet."""
    earlier = None
    if chip.reviewed_before():
        became = chip.earlier.became
        earlier = "earlier: " + ("Ignore" if became == IGNORE else became)
        if chip.verdict is None:
            return earlier
    if chip.verdict is None:
        if chip.labelled_already():
            return "already in project: " + chip.nearest().describe()
        return "no verdict yet"
    text = "verdict: " + ("Ignore" if chip.verdict == IGNORE
                          else chip.verdict)
    if earlier:
        return f"{text} ({earlier})"
    if chip.labelled_already() and chip.verdict != IGNORE:
        return text + (" (as a new label beside the one there)"
                       if chip.as_new else " (onto the label there)")
    return text


def length_text(image, p1, p2) -> str:
    """The length between two source-pixel points, as the Measure tool
    says it (2.8.12): metres by the image's georeferencing and pixels,
    or pixels alone when there is no georeferencing - or when it
    cannot be used, which must not stop anything."""
    px = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    pixels = f"{px:.1f} px"
    try:
        if image is None or image.get_affine() is None \
                or image.get_crs() is None:
            raise ValueError("no georeferencing")
        a = image.pixel_to_latlon(p1[0], p1[1])
        b = image.pixel_to_latlon(p2[0], p2[1])
        if a is None or b is None:
            raise ValueError("no ground")
        metres = geodesic_distance(a[0], a[1], b[0], b[1])
        if not math.isfinite(metres):
            raise ValueError("no distance")
    except Exception:  # noqa: BLE001 - a readout, never a crash
        return f"{pixels} (no georeferencing to give metres)"
    if metres >= 1000:
        ground = f"{metres / 1000:.2f} km"
    elif metres >= 10:
        ground = f"{metres:.1f} m"
    else:
        ground = f"{metres:.2f} m"
    return f"{ground} ({pixels})"


class ContextView(QWidget):
    """The context picture, the chip's box on it, and the centre mark -
    and, measuring, the line between two clicks (2.8.12)."""

    measured = pyqtSignal(object)   # (p1, p2) in picture pixels, or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._box: tuple | None = None      # (x, y, w, h) in picture pixels
        self._centre: tuple | None = None   # (column, row) in picture pixels
        self._moved = False
        self._marks: list = []          # (column, row, text): labels near
        self._reading = False
        self._failed = False            # the read came back empty
        self._measuring = False
        self._measure: list = []            # 0, 1 or 2 picture points
        self._measure_text = ""
        self._hover: tuple | None = None    # the cursor, while measuring
        self.setMinimumSize(120, 100)
        self.setMouseTracking(True)

    def clear(self):
        self._pixmap = None
        self._box = self._centre = None
        self._reading = self._failed = False
        self._drop_measure()
        self.update()

    def set_reading(self):
        """A chip is in hand and its picture is on its way."""
        self._pixmap = None
        self._box = self._centre = None
        self._reading, self._failed = True, False
        self._drop_measure()
        self.update()

    def set_failed(self):
        """The picture could not be read (an unreadable file, a
        window of nodata): said, instead of "Reading..." for good
        (2.8.14)."""
        self._pixmap = None
        self._box = self._centre = None
        self._reading, self._failed = False, True
        self._drop_measure()
        self.update()

    def message(self) -> str:
        """The words shown in place of a picture, or none."""
        if self._reading:
            return "Reading..."
        return COULD_NOT_READ if self._failed else ""

    # -- measuring (2.8.12) ------------------------------------------------

    def measuring(self) -> bool:
        return self._measuring

    def set_measuring(self, on: bool):
        """Two clicks on the picture then give the length between them;
        a right-click clears. Off, the line goes."""
        self._measuring = bool(on)
        self.setCursor(Qt.CrossCursor if self._measuring
                       else Qt.ArrowCursor)
        if not self._measuring:
            self._drop_measure()
        self.update()

    def clear_measure(self):
        self._drop_measure()
        self.update()
        self.measured.emit(None)

    def _drop_measure(self):
        self._measure = []
        self._measure_text = ""
        self._hover = None

    def set_measure_text(self, text: str):
        self._measure_text = text
        self.update()

    def picture_point(self, pos) -> "tuple | None":
        """The picture pixel under a widget position, or None off it."""
        geometry = self._geometry()
        if geometry is None:
            return None
        off_x, off_y, scale = geometry
        col = (pos.x() - off_x) / scale
        row = (pos.y() - off_y) / scale
        if not (0 <= col < self._pixmap.width()
                and 0 <= row < self._pixmap.height()):
            return None
        return (col, row)

    def mousePressEvent(self, event):
        if not self._measuring:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.RightButton:
            self.clear_measure()
            return
        if event.button() != Qt.LeftButton:
            return
        point = self.picture_point(event.localPos())   # to the fraction
        if point is None:
            return
        if len(self._measure) == 1:
            self._measure.append(point)
            self._hover = None
            self.update()
            self.measured.emit(tuple(self._measure))
        else:
            self._measure = [point]        # a new line starts
            self._measure_text = ""
            self.update()
            self.measured.emit(None)

    def mouseMoveEvent(self, event):
        if self._measuring and len(self._measure) == 1:
            self._hover = self.picture_point(event.localPos())
            self.update()
        super().mouseMoveEvent(event)

    def _paint_measure(self, painter, geometry):
        if not self._measuring or not self._measure:
            return
        off_x, off_y, scale = geometry

        def at(point):
            return QPointF(off_x + (point[0] + 0.5) * scale,
                           off_y + (point[1] + 0.5) * scale)
        start = at(self._measure[0])
        end = at(self._measure[1]) if len(self._measure) == 2 else (
            at(self._hover) if self._hover is not None else None)
        for colour, width in ((QColor(0, 0, 0, 210), 4), (MARK_COLOR, 2)):
            pen = QPen(colour, width)
            if end is not None and len(self._measure) < 2:
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawEllipse(start, 3, 3)
            if end is not None:
                painter.drawLine(start, end)
                painter.drawEllipse(end, 3, 3)
        if self._measure_text and end is not None:
            middle = QPointF((start.x() + end.x()) / 2,
                             (start.y() + end.y()) / 2)
            metrics = painter.fontMetrics()
            box = QRectF(middle.x() + 8, middle.y() - metrics.height() - 4,
                         metrics.horizontalAdvance(self._measure_text) + 8,
                         metrics.height() + 4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRect(box)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(MARK_COLOR)
            painter.drawText(box, Qt.AlignCenter, self._measure_text)

    def has_picture(self) -> bool:
        return self._pixmap is not None

    def set_marks(self, marks: list):
        """Rings with a word at these (column, row) of the picture: the
        labels already there (2.9.4)."""
        self._marks = [(float(c), float(r), str(text)) for c, r, text in marks]
        self.update()

    def set_picture(self, arr: np.ndarray, box: tuple, centre: tuple,
                    moved: bool):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self._box = tuple(float(v) for v in box)
        self._centre = (float(centre[0]), float(centre[1]))
        self._moved = bool(moved)
        self._reading = self._failed = False
        self._drop_measure()        # a new picture: the line meant the old
        self.update()

    def _geometry(self) -> "tuple | None":
        """(left, top, scale): the picture fitted in the widget."""
        if self._pixmap is None or self._pixmap.width() == 0:
            return None
        scale = min(self.width() / self._pixmap.width(),
                    self.height() / self._pixmap.height())
        shown_w = self._pixmap.width() * scale
        shown_h = self._pixmap.height() * scale
        return ((self.width() - shown_w) / 2.0,
                (self.height() - shown_h) / 2.0, scale)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        geometry = self._geometry()
        if geometry is None:
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(self.rect(), Qt.AlignCenter, self.message())
            painter.end()
            return
        off_x, off_y, scale = geometry
        target = QRectF(off_x, off_y, self._pixmap.width() * scale,
                        self._pixmap.height() * scale)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._pixmap,
                           QRectF(self._pixmap.rect()))
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        if self._box is not None:
            x, y, w, h = self._box
            painter.setPen(QPen(BOX_COLOR, 2))
            painter.drawRect(QRectF(off_x + x * scale, off_y + y * scale,
                                    w * scale, h * scale))
        if self._centre is not None:
            cx = off_x + (self._centre[0] + 0.5) * scale
            cy = off_y + (self._centre[1] + 0.5) * scale
            light = MOVED_MARK_COLOR if self._moved else MARK_COLOR
            for colour, width in ((QColor(0, 0, 0, 210), 4), (light, 2)):
                painter.setPen(QPen(colour, width))
                painter.drawEllipse(QPointF(cx, cy), MARK_RING_PX,
                                    MARK_RING_PX)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    painter.drawLine(
                        QPointF(cx + dx * MARK_GAP_PX, cy + dy * MARK_GAP_PX),
                        QPointF(cx + dx * (MARK_GAP_PX + MARK_PX),
                                cy + dy * (MARK_GAP_PX + MARK_PX)))
        for col, row, text in self._marks:
            mx = off_x + (col + 0.5) * scale
            my = off_y + (row + 0.5) * scale
            if not target.contains(QPointF(mx, my)):
                continue
            for colour, width in ((QColor(0, 0, 0, 210), 4),
                                  (NEAR_COLOR, 2)):
                painter.setPen(QPen(colour, width))
                painter.drawEllipse(QPointF(mx, my), NEAR_RING_PX,
                                    NEAR_RING_PX)
            metrics = painter.fontMetrics()
            box = QRectF(mx + NEAR_RING_PX + 3, my - metrics.height() / 2,
                         metrics.horizontalAdvance(text) + 6,
                         metrics.height())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRect(box)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(NEAR_COLOR)
            painter.drawText(box, Qt.AlignCenter, text)
        self._paint_measure(painter, (off_x, off_y, scale))
        painter.end()


class DetailPanel(QWidget):
    """The chip in hand: its surroundings and everything known of it."""

    show_on_image = pyqtSignal()
    context_changed = pyqtSignal(int)
    as_new_changed = pyqtSignal(bool)   # the tick below, for the chip in hand

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chip = None
        self._image = None              # the project's entry, for metres
        self._picture_frame = None      # the picture's top-left source pixel
        self._near: list = []           # the labels already there (2.9.4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Details")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.view = ContextView()
        layout.addWidget(self.view, 1)
        self.info = QLabel(NOTHING)
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.info)
        # The file's own words on the chip, when it has any (2.9.16), in
        # a box a few lines high that scrolls: a paragraph used to squeeze
        # the picture to its floor and was clipped besides (2.9.17). The
        # popup on the grid shows the whole text.
        self.description = QLabel("")
        self.description.setWordWrap(True)
        self.description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.description_box = QScrollArea()
        self.description_box.setWidget(self.description)
        self.description_box.setWidgetResizable(True)
        self.description_box.setFrameShape(QFrame.NoFrame)
        self.description_box.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff)
        self.description_box.viewport().setAutoFillBackground(False)
        self.description_box.setMaximumHeight(
            DESCRIPTION_LINES * self.description.fontMetrics().lineSpacing()
            + 6)
        self.description_box.setVisible(False)
        layout.addWidget(self.description_box)
        self.measure_info = QLabel("")
        self.measure_info.setWordWrap(True)
        self.measure_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.measure_info)
        # For a chip labelled already: its verdict goes onto the label
        # there unless this is ticked (2.9.4).
        self.as_new_check = QCheckBox("Add as a new label here")
        self.as_new_check.setToolTip(
            "Two objects close together: the verdict makes a new label at\n"
            "the chip's centre, beside the one already there, instead of\n"
            "going onto that label. Not dropped as a duplicate.")
        self.as_new_check.toggled.connect(self.as_new_changed)
        self.as_new_check.setVisible(False)
        layout.addWidget(self.as_new_check)
        self.view.measured.connect(self._on_measured)
        row = QHBoxLayout()
        row.addWidget(QLabel("Context"))
        self.context_spin = QSpinBox()
        self.context_spin.setRange(MIN_CONTEXT, MAX_CONTEXT)
        self.context_spin.setValue(DEFAULT_CONTEXT)
        self.context_spin.setSuffix(" chips")
        self.context_spin.setToolTip(
            "How much of the image round the chip the picture shows:\n"
            "this many chip widths across.")
        self.context_spin.setKeyboardTracking(False)
        self.context_spin.valueChanged.connect(self.context_changed)
        row.addWidget(self.context_spin)
        row.addStretch(1)
        self.measure_button = QPushButton("Measure (R)")
        self.measure_button.setCheckable(True)
        self.measure_button.setToolTip(
            "Two clicks on the picture give the length between them: in\n"
            "metres, by the image's georeferencing, and in pixels - pixels\n"
            "alone for an image without georeferencing. A right-click\n"
            "clears; press again to stop.")
        self.measure_button.toggled.connect(self._on_measure_toggled)
        self.image_button = QPushButton("Show on image (M)")
        self.image_button.setToolTip(
            "The whole image in a window of its own, with a box where\n"
            "this chip is.")
        self.image_button.clicked.connect(self.show_on_image)
        self.image_button.setEnabled(False)
        layout.addLayout(row)
        # The two buttons on a row of their own: beside the Context box
        # they did not fit the panel at its default width (2.8.13).
        buttons = QHBoxLayout()
        buttons.addWidget(self.measure_button)
        buttons.addWidget(self.image_button)
        layout.addLayout(buttons)
        self.setMinimumWidth(240)

    def chip(self):
        """The chip in hand, or None."""
        return self._chip

    def picture_failed(self):
        """The chip in hand's picture could not be read."""
        self._picture_frame = None
        self.measure_info.setText("")
        self.view.set_failed()

    def factor(self) -> int:
        return self.context_spin.value()

    def has_picture(self) -> bool:
        return self.view.has_picture()

    def clear(self):
        self._chip = None
        self._image = None
        self._picture_frame = None
        self._near = []
        self.view.clear()
        self.view.set_marks([])
        self.info.setText(NOTHING)
        self.measure_info.setText("")
        self.image_button.setEnabled(False)
        self.as_new_check.setVisible(False)
        self.description.setText("")
        self.description_box.setVisible(False)

    def near(self) -> list:
        """The labels already there, as last shown."""
        return list(self._near)

    def _update_marks(self):
        """The labels already there, on the picture, once there is one."""
        if self._picture_frame is None or not self._near:
            self.view.set_marks([])
            return
        px0, py0 = self._picture_frame
        self.view.set_marks([(n.pixel_x - px0, n.pixel_y - py0,
                              n.class_name) for n in self._near])

    # -- measuring (2.8.12) ------------------------------------------------

    def _on_measure_toggled(self, on: bool):
        self.view.set_measuring(on)
        if not on:
            self.measure_info.setText("")

    def _on_measured(self, points):
        """The view's two points, in picture pixels, or None."""
        if points is None or self._picture_frame is None:
            self.measure_info.setText("")
            self.view.set_measure_text("")
            return
        px0, py0 = self._picture_frame
        (c1, r1), (c2, r2) = points
        text = length_text(self._image, (px0 + c1, py0 + r1),
                           (px0 + c2, py0 + r2))
        self.measure_info.setText("Measured: " + text)
        self.view.set_measure_text(text.split(" (")[0])

    def show_chip(self, chip, latlon: "tuple | None", suggestion=None,
                  image=None, near=()):
        """Everything known of the chip; the picture follows when read.
        ``latlon`` is (lat, lon), or None for imagery without ground;
        ``suggestion`` what the model learned here thinks, if trained;
        ``image`` the project's entry, which gives Measure its metres;
        ``near`` the labels already there (labelled.Near), marked on
        the picture and listed."""
        if chip is not self._chip:
            self.view.set_reading()
            self.measure_info.setText("")
            self._picture_frame = None
        self._chip = chip
        self._image = image
        self._near = list(near)
        self.image_button.setEnabled(True)
        labelled = chip.labelled_already()
        self.as_new_check.blockSignals(True)
        self.as_new_check.setChecked(bool(chip.as_new))
        self.as_new_check.blockSignals(False)
        self.as_new_check.setVisible(labelled)
        self._update_marks()
        lines = [f"<b>{chip.image_name}</b>  ({chip.pixel_x:.1f}, "
                 f"{chip.pixel_y:.1f})"]
        if latlon is not None:
            lines.append(f"lat {latlon[0]:.6f}, lon {latlon[1]:.6f}")
        lines.append(called(chip))
        standing = standing_of(chip)
        if chip.reviewed_before():
            earlier = chip.earlier
            if earlier.by:
                standing += f" by {earlier.by}"
            if earlier.at:
                standing += f" on {earlier.at[:10]}"
            if earlier.source:
                standing += f" ({earlier.source})"
        lines.append(standing)
        if suggestion is not None:
            lines.append("suggested: " + suggestion.describe())
        if chip.moved():
            lines.append(f"centre moved {chip.offset():.0f} px from the "
                         f"file's ({chip.source_x:.1f}, {chip.source_y:.1f})")
        if chip.rescaled:
            lines.append("scaled from the size the file stated")
        if self._near:
            shown = self._near[:4]
            lines.append("<b>Labels already there:</b> " + "; ".join(
                f"{n.class_name} at ({n.pixel_x:.0f}, {n.pixel_y:.0f}), "
                f"{n.distance:.0f} px off" for n in shown)
                + (f"; and {len(self._near) - len(shown)} more"
                   if len(self._near) > len(shown) else ""))
            if chip.reviewed_before():
                pass                    # its own is the note's; these are others
            elif chip.verdict == IGNORE:
                lines.append("Ignored: the label there stays as it is.")
            elif chip.as_new:
                lines.append("A verdict makes a new label here, beside it.")
            else:
                lines.append("A verdict goes onto the nearest of them; tick "
                             "below for a new label here instead.")
        self.info.setText("<br>".join(lines))
        description = chip.description
        # Escaped, so it reads as written; its line breaks kept (2.9.17).
        self.description.setText(
            "<b>Description:</b> " + "<br>".join(
                html.escape(line, quote=False)
                for line in description.splitlines())
            if description else "")
        self.description_box.setVisible(bool(description))

    def set_picture(self, chip, arr: np.ndarray, picture_frame: tuple,
                    chip_frame: tuple):
        """The context picture of ``chip``, read with its top-left source
        pixel at ``picture_frame``; ``chip_frame`` is (x0, y0, w, h) of the
        chip's own window in source pixels. The window hands over only
        the chip in hand's."""
        px0, py0 = picture_frame
        self._picture_frame = (float(px0), float(py0))
        self.measure_info.setText("")
        x0, y0, w, h = chip_frame
        self.view.set_picture(arr, (x0 - px0, y0 - py0, w, h),
                              (chip.pixel_x - px0, chip.pixel_y - py0),
                              chip.moved())
        self._update_marks()
