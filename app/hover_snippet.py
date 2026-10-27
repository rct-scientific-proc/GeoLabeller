"""View > Hover Snippet (2.9.2): a label's snippet beside the cursor.

Hold Ctrl and rest the mouse on a label's marker: a small window shows
the snippet around that label, read the way the group's Display
Settings draw it, captioned with its class and image, for a quick look
without opening the Snippet Editor. It goes when the mouse leaves the
marker, Ctrl is released, or anything is clicked. Only with Ctrl held,
so a mouse crossing the canvas while panning raises nothing. The size
of the snippet - source pixels read, and pixels on screen - is chosen in
the View menu, Off among the choices, and remembered.

The canvas notices the rest and asks (label_hover_requested); the main
window, which has the project, reads the snippet off the UI thread
through a SnippetLoader and shows this window.
"""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from .settings_scope import settings as app_settings

HOVER_KEY = "view/hover_snippet_px"
HOVER_SIZES = (0, 96, 128, 192, 256)      # 0 is Off
DEFAULT_HOVER_PX = 128
OFFSET_PX = 16                            # from the cursor


def hover_px() -> int:
    """The snippet size chosen, or 0 for Off."""
    try:
        value = int(app_settings().value(HOVER_KEY, DEFAULT_HOVER_PX))
    except (TypeError, ValueError):
        return DEFAULT_HOVER_PX
    return value if value in HOVER_SIZES else DEFAULT_HOVER_PX


def set_hover_px(size: int):
    if size not in HOVER_SIZES:
        raise ValueError(size)
    app_settings().setValue(HOVER_KEY, int(size))


class HoverSnippet(QWidget):
    """The small window: the picture and a caption, placed beside the
    cursor and kept on the screen."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: #1e1e1e; color: white;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.picture)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.caption)
        self.label_id: int | None = None

    def show_at(self, where: QPoint, arr: np.ndarray, caption: str,
                label_id: int | None = None):
        """Show ``arr`` (an RGB array) at its own size, captioned, beside
        the screen point ``where``."""
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self.picture.setPixmap(QPixmap.fromImage(image))   # a copy
        self.caption.setText(caption)
        self.label_id = label_id
        self.adjustSize()
        self.move(self._place(where, self.sizeHint()))
        self.show()

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

    def hideEvent(self, event):
        self.label_id = None
        super().hideEvent(event)
