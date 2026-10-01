"""A section that folds away under its header.

Asked for 2026-10-01, for the left-hand side of the main window: Layers,
Labeled Images, Waypoints and Hard Negatives each took their share of the
column whether or not they were being used. Each now sits under a header
that opens and closes it, the way the folders of an editor's explorer do -
a chevron pointing down when open, right when closed.

CollapsibleSection is the header and its content; it knows nothing of
where it is placed. Closed, it is exactly as tall as its header, which is
what lets a splitter around it give the room to the sections still open
(see layer_panel.CombinedLayerPanel).
"""
from PyQt5.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PyQt5.QtWidgets import (QHBoxLayout, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

# Qt's "no maximum" for a widget's height.
_NO_MAXIMUM = 16777215
_CHEVRON_PX = 12


def chevron_icon(expanded: bool, colour: QColor) -> QIcon:
    """A chevron drawn in ``colour``: down when open, right when closed.

    Drawn, not typed: the glyphs fonts offer for this differ in size and
    weight from one machine to the next, and some have none.
    """
    pixmap = QPixmap(_CHEVRON_PX * 2, _CHEVRON_PX * 2)       # 2x, for
    pixmap.setDevicePixelRatio(2.0)                          # crisp edges
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(colour)
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    if expanded:
        points = (QPointF(2.5, 4.5), QPointF(6.0, 8.0), QPointF(9.5, 4.5))
    else:
        points = (QPointF(4.5, 2.5), QPointF(8.0, 6.0), QPointF(4.5, 9.5))
    painter.drawPolyline(*points)
    painter.end()
    return QIcon(pixmap)


class CollapsibleSection(QWidget):
    """A titled section: click the header to fold it to just the header."""

    # True when the section has just opened, False when it has closed.
    toggled = pyqtSignal(bool)

    def __init__(self, title: str, content: QWidget, parent=None,
                 description: str = ""):
        """``description`` says what the section holds, in the header's
        tooltip."""
        super().__init__(parent)
        self.content = content
        self._expanded = True
        self._description = description

        self.header = QPushButton(title)
        self.header.setFlat(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setIconSize(QSize(_CHEVRON_PX, _CHEVRON_PX))
        self.header.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; "
            "border: none; padding: 4px 4px; }"
            "QPushButton:hover { background: palette(midlight); }")
        self.header.clicked.connect(lambda: self.set_expanded(
            not self._expanded))

        self._header_row = QHBoxLayout()
        self._header_row.setContentsMargins(0, 0, 4, 0)
        self._header_row.setSpacing(4)
        self._header_row.addWidget(self.header, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._header_row)
        layout.addWidget(content, 1)
        self._show_state()

    # -- the header ----------------------------------------------------------

    def title(self) -> str:
        return self.header.text()

    def add_header_widget(self, widget: QWidget):
        """Put a control on the right of the header, where it stays in
        reach while the section is closed."""
        self._header_row.addWidget(widget)

    def header_height(self) -> int:
        """How tall the section is when closed."""
        height = self.header.sizeHint().height()
        for index in range(1, self._header_row.count()):
            widget = self._header_row.itemAt(index).widget()
            if widget is not None:
                height = max(height, widget.sizeHint().height())
        return height

    # -- open and closed -----------------------------------------------------

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._show_state()
        self.toggled.emit(expanded)

    def _show_state(self):
        self.content.setVisible(self._expanded)
        self.header.setIcon(chevron_icon(
            self._expanded, self.palette().color(QPalette.ButtonText)))
        action = (f"Click to {'close' if self._expanded else 'open'} "
                  f"{self.title()}.")
        self.header.setToolTip(
            f"{self._description}\n\n{action}" if self._description
            else action)
        # Closed, it is its header and no more: a splitter around it can
        # then neither drag it open nor leave it holding empty room.
        self.setMaximumHeight(
            _NO_MAXIMUM if self._expanded else self.header_height())
