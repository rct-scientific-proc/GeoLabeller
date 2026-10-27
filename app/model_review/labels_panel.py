"""The Labels panel (2.9.7): every class stacked on the left of the grid,
one click away.

Asked for 2026-10-27: picking a class from the New label dropdown -
open it, scroll, find the one - was slow with many classes, and the
users wanted them laid out to pick with the mouse. The panel lists
Ignore and then every class in the window's order, each with its
colour, the key that picks it - 1 to 9, then Shift+1 to Shift+9 for
the tenth to the eighteenth (2.9.8) - and how many chips an import
gives it. A click makes the class active and, as the dropdown does,
gives it to the picked chips or the chip under the cursor. A filter
box narrows the list as you type; Enter takes the first class left,
Escape clears the box. Nothing here keeps the keyboard from the grid:
the list and the button never take focus, and the box hands it back
on Enter or Escape.

A class dragged up or down the list (2.9.8) moves under another key:
the panel reports the new order and the window keeps it (class_order
.py). Ignore is not dragged and stays on top whatever is dropped
above it; while the filter shortens the list nothing is dragged, so
the order a drop makes is always the whole list's.

The window owns the classes, the colours, the order and the verdicts;
the panel shows them and reports a pick or a move.
"""
from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

from .ledger import IGNORE

IGNORE_COLOR = QColor(150, 150, 150)    # the window's shade of Ignore
MIN_WIDTH = 150
PREFERRED_WIDTH = 200                   # to start with; the handle moves
SHIFT = "⇧"                        # the key hint for Shift+1..9


def key_hint(index: int) -> str:
    """What picks the class at this place: 1-9, Shift+1-9 shown as an
    arrow and a digit, nothing beyond the eighteenth."""
    if index < 9:
        return str(index + 1)
    if index < 18:
        return SHIFT + str(index - 8)
    return " "


def swatch(colour: QColor) -> QIcon:
    """A small square of a class's colour."""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(QPen(QColor(40, 40, 40), 1))
    painter.setBrush(colour)
    painter.drawRect(1, 1, 11, 11)
    painter.end()
    return QIcon(pixmap)


class FilterEdit(QLineEdit):
    """The filter box: Escape clears it and gives the keyboard back."""

    escaped = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clear()
            self.escaped.emit()
            return
        super().keyPressEvent(event)


class ClassList(QListWidget):
    """The stacked classes: a drag moves one, Ignore stays on top."""

    reordered = pyqtSignal(list)        # the classes, top to bottom
    drag_done = pyqtSignal()            # a drag over, dropped or not

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    def names(self) -> list:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    def dropEvent(self, event):
        super().dropEvent(event)        # Qt moves the row (InternalMove)
        if event.isAccepted():
            self._after_drop()

    def startDrag(self, actions):
        super().startDrag(actions)
        self.drag_done.emit()

    def _after_drop(self):
        """Ignore back on top if something was dropped above it; then
        the classes' order, top to bottom, reported."""
        names = self.names()
        if IGNORE in names and names.index(IGNORE) != 0:
            self.insertItem(0, self.takeItem(names.index(IGNORE)))
        self.reordered.emit([name for name in self.names() if name != IGNORE])


class LabelsPanel(QWidget):
    """See the module."""

    MIN_WIDTH = MIN_WIDTH
    picked = pyqtSignal(object)         # a class name, or IGNORE
    add_requested = pyqtSignal()        # Add class...
    released = pyqtSignal()             # the keyboard is the grid's again
    reordered = pyqtSignal(list)        # the classes dragged into this order

    def __init__(self, parent=None):
        super().__init__(parent)
        self._classes: list = []
        self._colours: list = []
        self._counts: dict = {}
        self._active = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Labels")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.filter = FilterEdit()
        self.filter.setPlaceholderText("Filter classes")
        self.filter.setClearButtonEnabled(True)
        self.filter.setToolTip(
            "Type part of a class name to shorten the list. Enter takes\n"
            "the first class left and Escape clears the box; either way\n"
            "the keys are the grid's again.")
        self.filter.textChanged.connect(lambda _t: self._rebuild())
        self.filter.returnPressed.connect(self._pick_first)
        self.filter.escaped.connect(self.released)
        layout.addWidget(self.filter)
        self.list = ClassList()
        self.list.setToolTip(
            "Click a class: it is the active label, and the picked chips\n"
            "- or the chip under the cursor - take it. The number is the\n"
            "key that picks it (an arrow means Shift held); the count is\n"
            "the chips an import gives it. Drag a class up or down to put\n"
            "it under the key you want - Ignore stays on top, and the\n"
            "order is yours from then on. Clear the filter box to drag.")
        self.list.itemClicked.connect(self._on_item)
        self.list.reordered.connect(self.reordered)
        self.list.drag_done.connect(self._highlight)
        layout.addWidget(self.list, 1)
        self.add_button = QPushButton("Add class...")
        self.add_button.setFocusPolicy(Qt.NoFocus)
        self.add_button.setToolTip(
            "A class the project does not have yet, added and made active.")
        self.add_button.clicked.connect(self.add_requested)
        layout.addWidget(self.add_button)
        self.setMinimumWidth(MIN_WIDTH)

    def sizeHint(self) -> QSize:
        return QSize(PREFERRED_WIDTH, super().sizeHint().height())

    # -- data in --------------------------------------------------------------

    def set_classes(self, names, colours=()):
        self._classes = list(names)
        self._colours = list(colours or [])
        self._rebuild()

    def set_counts(self, counts: dict):
        """How many chips an import gives each verdict (IGNORE among
        them)."""
        counts = {k: int(v) for k, v in dict(counts).items() if v}
        if counts != self._counts:
            self._counts = counts
            self._rebuild()

    def set_active(self, verdict):
        self._active = verdict
        self._highlight()

    # -- what is shown --------------------------------------------------------

    def rows(self) -> list:
        """[(verdict, text)] as listed, top to bottom."""
        return [(self.list.item(i).data(Qt.UserRole), self.list.item(i).text())
                for i in range(self.list.count())]

    def _colour_of(self, name):
        if name == IGNORE:
            return IGNORE_COLOR
        if name in self._classes and self._colours:
            return self._colours[self._classes.index(name)
                                 % len(self._colours)]
        return None

    def _rebuild(self):
        needle = self.filter.text().strip().lower()
        self.list.clear()
        # A drag while the list is shortened would order a part of it.
        self.list.setDragEnabled(not needle)
        entries = [(IGNORE, "Ignore", "0")]
        entries += [(name, name, key_hint(i))
                    for i, name in enumerate(self._classes)]
        for verdict, name, key in entries:
            if needle and needle not in name.lower():
                continue
            text = f"{key}  {name}"
            count = self._counts.get(verdict, 0)
            if count:
                text += f"  ({count})"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, verdict)
            # Dragged, never dropped onto: a drop lands between rows.
            flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
            if verdict != IGNORE:
                flags |= Qt.ItemIsDragEnabled
            item.setFlags(flags)
            colour = self._colour_of(verdict)
            if colour is not None:
                item.setIcon(swatch(colour))
            self.list.addItem(item)
        self._highlight()

    def _highlight(self):
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == self._active:
                self.list.setCurrentItem(item)
                return
        self.list.setCurrentItem(None)

    # -- picks ----------------------------------------------------------------

    def _on_item(self, item):
        self.picked.emit(item.data(Qt.UserRole))

    def _pick_first(self):
        """Enter in the filter: the first class left, Ignore only when
        it is the only one; the filter is cleared either way, and with
        nothing left the keyboard is simply given back."""
        verdicts = [verdict for verdict, _text in self.rows()]
        classes = [verdict for verdict in verdicts if verdict != IGNORE]
        first = classes[0] if classes else (verdicts[0] if verdicts else None)
        self.filter.clear()
        if first is None:
            self.released.emit()
        else:
            self.picked.emit(first)
