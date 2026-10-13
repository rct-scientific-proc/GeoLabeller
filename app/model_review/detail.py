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

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from .ledger import IGNORE

DEFAULT_CONTEXT = 3             # the picture is this many chips across
MIN_CONTEXT, MAX_CONTEXT = 2, 8
BOX_COLOR = QColor(255, 200, 0)
MARK_COLOR = QColor(255, 255, 255, 230)
MOVED_MARK_COLOR = QColor(0, 220, 255, 240)
MARK_RING_PX = 6
MARK_GAP_PX = 9
MARK_PX = 9
NOTHING = "No chip in hand: move the cursor onto one, or click one."


def standing_of(chip) -> str:
    """What the chip is, in a few words: its verdict, what it became in
    an earlier round, or nothing yet."""
    if chip.reviewed_before():
        became = chip.earlier.became
        return "earlier: " + ("Ignore" if became == IGNORE else became)
    if chip.verdict is None:
        return "no verdict yet"
    return "verdict: " + ("Ignore" if chip.verdict == IGNORE
                          else chip.verdict)


class ContextView(QWidget):
    """The context picture, the chip's box on it, and the centre mark."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._box: tuple | None = None      # (x, y, w, h) in picture pixels
        self._centre: tuple | None = None   # (column, row) in picture pixels
        self._moved = False
        self._reading = False
        self.setMinimumSize(120, 100)

    def clear(self):
        self._pixmap = None
        self._box = self._centre = None
        self._reading = False
        self.update()

    def set_reading(self):
        """A chip is in hand and its picture is on its way."""
        self._pixmap = None
        self._box = self._centre = None
        self._reading = True
        self.update()

    def has_picture(self) -> bool:
        return self._pixmap is not None

    def set_picture(self, arr: np.ndarray, box: tuple, centre: tuple,
                    moved: bool):
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self._box = tuple(float(v) for v in box)
        self._centre = (float(centre[0]), float(centre[1]))
        self._moved = bool(moved)
        self._reading = False
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
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Reading..." if self._reading else "")
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
        painter.end()


class DetailPanel(QWidget):
    """The chip in hand: its surroundings and everything known of it."""

    show_on_image = pyqtSignal()
    context_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chip = None
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
        self.image_button = QPushButton("Show on image (M)")
        self.image_button.setToolTip(
            "The whole image in a window of its own, with a box where\n"
            "this chip is.")
        self.image_button.clicked.connect(self.show_on_image)
        self.image_button.setEnabled(False)
        row.addWidget(self.image_button)
        layout.addLayout(row)
        self.setMinimumWidth(240)

    def chip(self):
        """The chip in hand, or None."""
        return self._chip

    def factor(self) -> int:
        return self.context_spin.value()

    def has_picture(self) -> bool:
        return self.view.has_picture()

    def clear(self):
        self._chip = None
        self.view.clear()
        self.info.setText(NOTHING)
        self.image_button.setEnabled(False)

    def show_chip(self, chip, latlon: "tuple | None", suggestion=None):
        """Everything known of the chip; the picture follows when read.
        ``latlon`` is (lat, lon), or None for imagery without ground;
        ``suggestion`` what the model learned here thinks, if trained."""
        if chip is not self._chip:
            self.view.set_reading()
        self._chip = chip
        self.image_button.setEnabled(True)
        lines = [f"<b>{chip.image_name}</b>  ({chip.pixel_x:.1f}, "
                 f"{chip.pixel_y:.1f})"]
        if latlon is not None:
            lines.append(f"lat {latlon[0]:.6f}, lon {latlon[1]:.6f}")
        lines.append(f"model: {chip.predicted} {chip.score:.2f}")
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
        self.info.setText("<br>".join(lines))

    def set_picture(self, chip, arr: np.ndarray, picture_frame: tuple,
                    chip_frame: tuple):
        """The context picture of ``chip``, read with its top-left source
        pixel at ``picture_frame``; ``chip_frame`` is (x0, y0, w, h) of the
        chip's own window in source pixels. The window hands over only
        the chip in hand's."""
        px0, py0 = picture_frame
        x0, y0, w, h = chip_frame
        self.view.set_picture(arr, (x0 - px0, y0 - py0, w, h),
                              (chip.pixel_x - px0, chip.pixel_y - py0),
                              chip.moved())
