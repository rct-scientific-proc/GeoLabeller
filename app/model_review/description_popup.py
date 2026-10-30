"""The description a review file gives a chip, shown beside the cursor.

A mis-classification file may say, per chip, why the model's call is
worth a look - a "description" (or "note", "comment", "reason") on the
record (2.9.16). When the mouse rests on such a chip the grid shows it
here: a frameless dark window beside the cursor, as the canvas's hover
snippet looks, the chip's own line above the text. It goes when the
mouse leaves the chip, on any click, wheel or key, and when the grid is
rebuilt. A chip without a description keeps its plain tooltip; one
with a description has this instead, so the two never show together.
"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

REST_MS = 400          # the mouse rests on a chip this long first
WRAP_PX = 360          # the text wraps at this width
OFFSET_PX = 16         # from the cursor


class DescriptionPopup(QWidget):
    """The small window: the chip's line, and the description under it."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: #1e1e1e; color: white;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self.heading = QLabel()
        self.heading.setStyleSheet("color: #b0b0b0;")
        layout.addWidget(self.heading)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(WRAP_PX)
        layout.addWidget(self.text)
        self.key: int | None = None     # the chip shown, or None
        self._watching = False

    def show_for(self, key: int, heading: str, text: str, where: QPoint):
        """Show ``text`` for chip ``key`` beside the screen point
        ``where``, ``heading`` (the chip's tooltip line) above it."""
        self.key = key
        self.heading.setText(heading)
        self.heading.setVisible(bool(heading))
        self.text.setText(text)
        self.adjustSize()
        self.move(self._place(where, self.sizeHint()))
        self.show()
        if not self._watching:
            # Any click, wheel or key anywhere takes it down.
            QApplication.instance().installEventFilter(self)
            self._watching = True

    @staticmethod
    def _place(where: QPoint, size) -> QPoint:
        """Below and right of the cursor, or on the other side of it
        where that would leave the screen."""
        x = where.x() + OFFSET_PX
        y = where.y() + OFFSET_PX
        screen = QApplication.screenAt(where) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            if x + size.width() > area.right():
                x = where.x() - OFFSET_PX - size.width()
            if y + size.height() > area.bottom():
                y = where.y() - OFFSET_PX - size.height()
            x = max(area.left(), x)
            y = max(area.top(), y)
        return QPoint(x, y)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.Wheel,
                            QEvent.KeyPress):
            self.hide()
        return False

    def hideEvent(self, event):
        self.key = None
        if self._watching:
            QApplication.instance().removeEventFilter(self)
            self._watching = False
        super().hideEvent(event)
