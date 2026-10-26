"""Show on image: a chip's place on the whole of its image.

A window of its own (asked for 2026-10-13), showing the image decimated
to fit, drawn the group's way, with a box the size of the chip round the
chip's centre and a mark at the centre itself. For a shadow or a wake the
surrounding scene is the strongest evidence there is. The wheel zooms
about the pointer, a drag pans, F fits the image again, Escape closes.
One window serves every chip; opening it for a chip on the same image
keeps the zoom and scrolls the box into view.

The image is read off the GUI thread (a decimated read of a large file
without overviews takes seconds), the newest request winning, and the
last few images read are kept: a list sorted by score hops between
images, and back.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import (QObject, QRectF, QRunnable, Qt, QThreadPool,
                          pyqtSignal)
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QGraphicsItem, QGraphicsItemGroup,
                             QGraphicsLineItem, QGraphicsPixmapItem,
                             QGraphicsRectItem, QGraphicsScene,
                             QGraphicsView, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from .. import display_settings
from ..snippets import read_image_overview, snippet_frame
from .source import called

BOX_COLOR = QColor(255, 200, 0)
MARK_COLOR = QColor(255, 255, 255)
MOVED_MARK_COLOR = QColor(0, 220, 255)     # the centre was moved
HALO_COLOR = QColor(0, 0, 0, 200)
MARK_GAP_PX = 6                 # the mark's arms start here from the centre...
MARK_PX = 14                    # ...and run this far, on screen at any zoom
ZOOM_STEP = 1.25
MIN_ZOOM, MAX_ZOOM = 0.02, 64.0
MAX_SIDE = 1600                 # the image is read no larger than this
KEPT_IMAGES = 4                 # overviews kept, for hopping back


class _Signals(QObject):
    finished = pyqtSignal(str, object, float)   # path, array or None, factor


class _OverviewRunnable(QRunnable):

    def __init__(self, path: str, max_side: int, signals: _Signals):
        super().__init__()
        self._path, self._max_side, self._signals = path, max_side, signals
        self.setAutoDelete(True)

    def run(self):
        try:
            arr, factor = read_image_overview(self._path, self._max_side)
        except Exception:  # noqa: BLE001 - a bad file is said, not raised
            arr, factor = None, 1.0
        self._signals.finished.emit(self._path, arr, factor)


class OverviewReader(QObject):
    """Reads whole images off the GUI thread; the newest request wins,
    and the last few read are kept."""

    ready = pyqtSignal(str, object, float)    # path, (H, W, 3) or None, factor

    def __init__(self, parent=None, max_side: int = MAX_SIDE):
        super().__init__(parent)
        self._max_side = max_side
        self._wanted: str | None = None
        self._kept: OrderedDict = OrderedDict()   # (path, display) -> (arr, f)
        self._signals_alive: set = set()

    def request(self, path: str):
        self._wanted = path
        kept = self._kept.get(self._key(path))
        if kept is not None:
            self._kept.move_to_end(self._key(path))
            self._wanted = None
            self.ready.emit(path, kept[0], kept[1])
            return
        signals = _Signals()
        self._signals_alive.add(signals)
        signals.finished.connect(
            lambda p, a, f, s=signals: self._on_finished(p, a, f, s))
        QThreadPool.globalInstance().start(
            _OverviewRunnable(path, self._max_side, signals))

    def cancel(self):
        self._wanted = None

    def pending(self) -> bool:
        return self._wanted is not None

    @staticmethod
    def _key(path: str):
        # The display settings are part of what the image looks like.
        return (path, display_settings.for_image(path))

    def _on_finished(self, path, arr, factor, signals):
        self._signals_alive.discard(signals)
        if sip.isdeleted(self):
            return                      # read finished after the window went
        if arr is not None:
            self._kept[self._key(path)] = (arr, factor)
            while len(self._kept) > KEPT_IMAGES:
                self._kept.popitem(last=False)
        if path != self._wanted:
            return                      # superseded; a newer read is coming
        self._wanted = None
        self.ready.emit(path, arr, factor)


class OverviewView(QGraphicsView):
    """The image: wheel to zoom about the pointer, drag to pan."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHints(QPainter.SmoothPixmapTransform
                            | QPainter.Antialiasing)
        self.setBackgroundBrush(QColor(24, 24, 24))
        self._fit_pending = False

    def wheelEvent(self, event):
        factor = ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP
        if MIN_ZOOM <= self.zoom() * factor <= MAX_ZOOM:
            self.scale(factor, factor)
        event.accept()

    def fit(self):
        """The whole image in the view. Before the view has a size the
        fit is done again at its first resize."""
        rect = self.sceneRect()
        if rect.isEmpty():
            return
        self.fitInView(rect, Qt.KeepAspectRatio)
        self._fit_pending = not self.isVisible()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_pending and self.isVisible():
            self.fit()

    def zoom(self) -> float:
        """Screen pixels per scene pixel."""
        return float(self.transform().m11())


class ImageWindow(QWidget):
    """The whole image, with a box where the chip is."""

    def __init__(self, parent=None, reader: "OverviewReader | None" = None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Show on image")
        self._reader = reader if reader is not None else OverviewReader(self)
        self._reader.ready.connect(self._on_overview)
        self._path: str | None = None
        self._display = None            # drawn with these settings
        self._factor = 1.0
        self._chip = None
        self._size = 0
        self._standing = ""
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel("")
        self.info.setWordWrap(True)
        top.addWidget(self.info, 1)
        self.fit_button = QPushButton("Fit (F)")
        self.fit_button.setToolTip("The whole image in the window again. "
                                   "The wheel zooms, a drag pans.")
        self.fit_button.clicked.connect(self.fit)
        top.addWidget(self.fit_button)
        layout.addLayout(top)
        self.view = OverviewView()
        layout.addWidget(self.view, 1)
        self._picture = QGraphicsPixmapItem()
        self.view.scene().addItem(self._picture)
        box_pen = QPen(BOX_COLOR, 2)
        box_pen.setCosmetic(True)
        self._box = QGraphicsRectItem()
        self._box.setPen(box_pen)
        self._box.setVisible(False)
        self.view.scene().addItem(self._box)
        # The mark keeps its size on screen at any zoom: its arms are
        # drawn in device pixels about the group's position.
        self._mark = QGraphicsItemGroup()
        self._mark.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self._arms = []
        for colour, width in ((HALO_COLOR, 4), (MARK_COLOR, 2)):
            pen = QPen(colour, width)
            pen.setCosmetic(True)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                arm = QGraphicsLineItem(
                    dx * MARK_GAP_PX, dy * MARK_GAP_PX,
                    dx * (MARK_GAP_PX + MARK_PX),
                    dy * (MARK_GAP_PX + MARK_PX))
                arm.setPen(pen)
                self._mark.addToGroup(arm)
                if width == 2:
                    self._arms.append(arm)
        self._mark.setVisible(False)
        self.view.scene().addItem(self._mark)
        self.resize(960, 760)

    # -- what is shown --------------------------------------------------------

    def path(self) -> str | None:
        """The image up now, or None."""
        return self._path

    def factor(self) -> float:
        """Image pixels per picture pixel."""
        return self._factor

    def box_rect(self) -> QRectF:
        """The chip's box, in picture pixels."""
        return self._box.rect()

    def mark_pos(self) -> tuple:
        """The centre mark, in picture pixels."""
        return (self._mark.pos().x(), self._mark.pos().y())

    def chip(self):
        """The chip shown, or None."""
        return self._chip

    def refresh(self, chip_size: int, standing: str = ""):
        """The box, mark and line again for the chip shown - its
        verdict, centre or the chip size changed - without the window
        coming forward."""
        if self._chip is None or self._chip.image_path != self._path:
            return
        self._size, self._standing = int(chip_size), standing
        self._place(bring=False)

    def fit(self):
        self.view.fit()

    def show_chip(self, chip, chip_size: int, standing: str = ""):
        """This chip's image - read first if it is not the one up - with
        the box where the chip is. ``standing`` is a word on its verdict
        for the line above the picture. The window comes up, or forward."""
        self._chip, self._size, self._standing = chip, int(chip_size), standing
        self.show()
        self.raise_()
        self.activateWindow()
        name = Path(chip.image_path).name
        self.setWindowTitle(f"Show on image - {name}")
        # The image up, drawn as its group is drawn now; the display
        # settings changed since, it is read again (2.8.14).
        if (chip.image_path == self._path and
                display_settings.for_image(chip.image_path) == self._display):
            self._place(bring=True)
            return
        self._box.setVisible(False)
        self._mark.setVisible(False)
        self.info.setText(f"Reading {name}...")
        self._reader.request(chip.image_path)

    def install_overview(self, path: str, arr, factor: float):
        """The image's picture as read: ``arr`` (H, W, 3) uint8, one of
        its pixels ``factor`` of the image's. Fitted to the view."""
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._picture.setPixmap(QPixmap.fromImage(image))
        self.view.scene().setSceneRect(0, 0, w, h)
        self._path, self._factor = path, float(factor)
        self._display = display_settings.for_image(path)
        self.view.resetTransform()
        self.view.fit()

    def _on_overview(self, path, arr, factor):
        if self._chip is None or path != self._chip.image_path:
            return
        if arr is None:
            self.info.setText(f"Could not read {Path(path).name}.")
            return
        self.install_overview(path, arr, factor)
        self._place(bring=False)

    def _place(self, bring: bool):
        """The box and the mark where the chip is on the picture up."""
        chip, f = self._chip, self._factor
        pixmap = self._picture.pixmap()
        width = chip.image_width or int(round(pixmap.width() * f))
        height = chip.image_height or int(round(pixmap.height() * f))
        x0, y0, w, h = snippet_frame(chip.pixel_x, chip.pixel_y, self._size,
                                     width, height)
        self._box.setRect(QRectF(x0 / f, y0 / f, w / f, h / f))
        self._box.setVisible(True)
        self._mark.setPos(chip.pixel_x / f, chip.pixel_y / f)
        pen = QPen(MOVED_MARK_COLOR if chip.moved() else MARK_COLOR, 2)
        pen.setCosmetic(True)
        for arm in self._arms:
            arm.setPen(pen)
        self._mark.setVisible(True)
        text = (f"{Path(chip.image_path).name}: chip at "
                f"({chip.pixel_x:.1f}, {chip.pixel_y:.1f}), "
                + called(chip))
        if self._standing:
            text += f"; {self._standing}"
        self.info.setText(text)
        if bring:
            # The view may be zoomed in on another part of the image.
            self.view.ensureVisible(self._box.rect(), 48, 48)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() == Qt.Key_F:
            self.fit()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._reader.cancel()
        super().closeEvent(event)
