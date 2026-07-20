"""Axis rulers for displaying lat/lon coordinates around the map canvas."""
import math
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt5.QtGui import QPainter, QPen, QFont, QFontMetrics
from PyQt5.QtCore import Qt


def _nice_interval(range_val: float) -> float:
    """Return a rounded tick interval (1/2/5 x 10^n) covering a range.

    Targets roughly 5-10 ticks across the range.
    """
    if range_val <= 0:
        return 1

    rough_interval = range_val / 7
    magnitude = math.pow(10, math.floor(math.log10(rough_interval)))
    normalized = rough_interval / magnitude

    if normalized < 1.5:
        nice = 1
    elif normalized < 3:
        nice = 2
    elif normalized < 7:
        nice = 5
    else:
        nice = 10

    return nice * magnitude


class AxisRuler(QWidget):
    """A ruler widget that displays coordinate tick marks."""

    RULER_SIZE = 30  # Width/height of the ruler in pixels

    def __init__(self, orientation: Qt.Orientation, canvas):
        """Initialize the ruler for the given orientation and canvas."""
        super().__init__()
        self.orientation = orientation
        self.canvas = canvas

        if orientation == Qt.Horizontal:
            self.setFixedHeight(self.RULER_SIZE)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setFixedWidth(self.RULER_SIZE)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self.setFont(QFont("Arial", 8))

    def paintEvent(self, event):
        """Draw the ruler with tick marks and labels."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.fillRect(self.rect(), Qt.white)

        # Border
        pen = QPen(Qt.darkGray)
        pen.setWidth(1)
        painter.setPen(pen)

        if self.orientation == Qt.Horizontal:
            painter.drawLine(
                0,
                self.height() - 1,
                self.width(),
                self.height() - 1)
            self._draw_horizontal_ticks(painter)
        else:
            painter.drawLine(
                self.width() - 1,
                0,
                self.width() - 1,
                self.height())
            self._draw_vertical_ticks(painter)

        painter.end()

    def _view_is_rotated(self) -> bool:
        """True when the view is rotated, making lat/lon ticks meaningless.

        Once rotated (image-up cycle mode) a constant longitude is no longer a
        vertical screen line, so the ruler blanks itself rather than drawing
        misleading ticks. The widget keeps its size so the layout is stable.
        """
        return abs(getattr(self.canvas, "view_rotation", lambda: 0.0)()) > 1e-6

    def _draw_horizontal_ticks(self, painter: QPainter):
        """Draw longitude tick marks."""
        if not self.canvas._layers or self._view_is_rotated():
            return

        view_bounds = self.canvas._get_view_bounds()
        west, south, east, north = view_bounds

        # Convert to lat/lon
        lon_west, _ = self.canvas._web_mercator_to_wgs84(west, 0)
        lon_east, _ = self.canvas._web_mercator_to_wgs84(east, 0)

        # Clamp to valid range
        lon_west = max(-180, min(180, lon_west))
        lon_east = max(-180, min(180, lon_east))

        if lon_east <= lon_west:
            return

        # Calculate nice tick interval
        lon_range = lon_east - lon_west
        tick_interval = self._nice_interval(lon_range)

        # Find first tick
        first_tick = math.ceil(lon_west / tick_interval) * tick_interval

        # Draw ticks
        pen = QPen(Qt.black)
        painter.setPen(pen)
        fm = QFontMetrics(self.font())

        lon = first_tick
        while lon <= lon_east:
            # Convert lon to screen x
            x = self._lon_to_screen_x(lon)

            if 0 <= x <= self.width():
                # Draw tick
                painter.drawLine(
                    int(x),
                    self.height() - 8,
                    int(x),
                    self.height() - 1)

                # Draw label
                label = self._format_lon(lon)
                label_width = fm.horizontalAdvance(label)
                painter.drawText(int(x - label_width / 2),
                                 self.height() - 12, label)

            lon += tick_interval

    def _draw_vertical_ticks(self, painter: QPainter):
        """Draw latitude tick marks."""
        if not self.canvas._layers or self._view_is_rotated():
            return

        view_bounds = self.canvas._get_view_bounds()
        west, south, east, north = view_bounds

        # Convert to lat/lon
        _, lat_south = self.canvas._web_mercator_to_wgs84(0, south)
        _, lat_north = self.canvas._web_mercator_to_wgs84(0, north)

        # Clamp to valid range
        lat_south = max(-85, min(85, lat_south))
        lat_north = max(-85, min(85, lat_north))

        if lat_north <= lat_south:
            return

        # Calculate nice tick interval
        lat_range = lat_north - lat_south
        tick_interval = self._nice_interval(lat_range)

        # Find first tick
        first_tick = math.ceil(lat_south / tick_interval) * tick_interval

        # Draw ticks
        pen = QPen(Qt.black)
        painter.setPen(pen)
        fm = QFontMetrics(self.font())

        lat = first_tick
        while lat <= lat_north:
            # Convert lat to screen y
            y = self._lat_to_screen_y(lat)

            if 0 <= y <= self.height():
                # Draw tick
                painter.drawLine(
                    self.width() - 8,
                    int(y),
                    self.width() - 1,
                    int(y))

                # Draw label (rotated)
                label = self._format_lat(lat)
                label_width = fm.horizontalAdvance(label)

                painter.save()
                painter.translate(self.width() - 12, y + label_width / 2)
                painter.rotate(-90)
                painter.drawText(0, 0, label)
                painter.restore()

            lat += tick_interval

    def _lon_to_screen_x(self, lon: float) -> float:
        """Convert longitude to screen x coordinate."""
        # Convert lon to Web Mercator
        R = 6378137.0
        x_mercator = R * math.radians(lon)

        # Convert to scene coordinates
        scene_x = x_mercator

        # Map scene to viewport
        view_pos = self.canvas.mapFromScene(scene_x, 0)

        # Account for ruler offset (canvas is offset by ruler width)
        return view_pos.x()

    def _lat_to_screen_y(self, lat: float) -> float:
        """Convert latitude to screen y coordinate."""
        # Convert lat to Web Mercator
        R = 6378137.0
        lat_rad = math.radians(lat)
        y_mercator = R * math.log(math.tan(math.pi / 4 + lat_rad / 2))

        # Convert to scene coordinates (Y is flipped)
        scene_y = -y_mercator

        # Map scene to viewport
        view_pos = self.canvas.mapFromScene(0, scene_y)

        return view_pos.y()

    def _nice_interval(self, range_val: float) -> float:
        """Calculate a nice tick interval for the given range."""
        return _nice_interval(range_val)

    def _format_lon(self, lon: float) -> str:
        """Format longitude for display."""
        if lon >= 0:
            return f"{lon:.6f}°E"
        else:
            return f"{-lon:.6f}°W"

    def _format_lat(self, lat: float) -> str:
        """Format latitude for display."""
        if lat >= 0:
            return f"{lat:.6f}°N"
        else:
            return f"{-lat:.6f}°S"


class MeterRuler(QWidget):
    """Ruler showing ground distance in metres across the visible canvas.

    The origin (0) is the canvas viewport's top-left corner; values increase to
    the right (horizontal ruler) and downward (vertical ruler), so the pair
    reads out how large the visible canvas is on the ground. Unlike the lat/lon
    rulers these values don't move with panning - they always measure the
    current view extent.
    """

    RULER_SIZE = 26  # Width/height of the ruler in pixels

    def __init__(self, orientation: Qt.Orientation, canvas):
        """Initialize the metre ruler for the given orientation and canvas."""
        super().__init__()
        self.orientation = orientation
        self.canvas = canvas

        if orientation == Qt.Horizontal:
            self.setFixedHeight(self.RULER_SIZE)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setFixedWidth(self.RULER_SIZE)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self.setFont(QFont("Arial", 7))

    def paintEvent(self, event):
        """Draw the metre ruler with tick marks and labels."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.white)

        pen = QPen(Qt.darkGray)
        pen.setWidth(1)
        painter.setPen(pen)
        if self.orientation == Qt.Horizontal:
            painter.drawLine(0, self.height() - 1, self.width(),
                             self.height() - 1)
        else:
            painter.drawLine(self.width() - 1, 0, self.width() - 1,
                             self.height())

        self._draw_ticks(painter)
        painter.end()

    def _draw_ticks(self, painter: QPainter):
        """Draw metre tick marks measured from the canvas's top-left corner."""
        # True ground metres per view pixel (cos-latitude corrected).
        mpp = self.canvas.view_ground_resolution()
        if mpp <= 0:
            return

        extent_px = (self.width() if self.orientation == Qt.Horizontal
                     else self.height())
        if extent_px <= 0:
            return

        total_m = extent_px * mpp
        interval = _nice_interval(total_m)
        if interval <= 0:
            return

        painter.setPen(QPen(Qt.black))
        fm = QFontMetrics(self.font())

        step = 0
        while True:
            meters = step * interval
            pos = meters / mpp  # pixels from the origin edge
            if pos > extent_px:
                break
            label = self._format_meters(meters)
            label_w = fm.horizontalAdvance(label)

            if self.orientation == Qt.Horizontal:
                x = int(pos)
                painter.drawLine(x, self.height() - 7, x, self.height() - 1)
                # Keep the label inside the widget at both ends.
                tx = max(0, min(int(x - label_w / 2), self.width() - label_w))
                painter.drawText(tx, self.height() - 9, label)
            else:
                y = int(pos)
                painter.drawLine(self.width() - 7, y, self.width() - 1, y)
                painter.save()
                painter.translate(self.width() - 9,
                                  min(y + label_w / 2, self.height()))
                painter.rotate(-90)
                painter.drawText(0, 0, label)
                painter.restore()
            step += 1

    @staticmethod
    def _format_meters(meters: float) -> str:
        """Format a distance in metres compactly (m below 1 km, else km)."""
        if meters == 0:
            return "0"
        if meters >= 1000:
            return f"{meters / 1000:g} km"
        if meters >= 1:
            return f"{meters:g} m"
        return f"{meters:.2f} m"


class MapCanvasWithAxes(QWidget):
    """Widget that combines MapCanvas with axis rulers."""

    def __init__(self, canvas):
        """Initialize the composite widget wrapping the canvas with rulers."""
        super().__init__()
        self.canvas = canvas

        # Create rulers: lat/lon next to the canvas, metres outside them.
        self.h_ruler = AxisRuler(Qt.Horizontal, canvas)
        self.v_ruler = AxisRuler(Qt.Vertical, canvas)
        self.h_meter_ruler = MeterRuler(Qt.Horizontal, canvas)
        self.v_meter_ruler = MeterRuler(Qt.Vertical, canvas)

        # Corner fillers must span both vertical rulers so the horizontal
        # rulers stay aligned with the canvas.
        left_width = MeterRuler.RULER_SIZE + AxisRuler.RULER_SIZE

        def _corner(height):
            """Build a blank white corner filler of the given height."""
            w = QWidget()
            w.setFixedSize(left_width, height)
            w.setStyleSheet("background-color: white;")
            return w

        # Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Row 1: corner + horizontal metre ruler (outermost)
        meter_row = QHBoxLayout()
        meter_row.setContentsMargins(0, 0, 0, 0)
        meter_row.setSpacing(0)
        meter_row.addWidget(_corner(MeterRuler.RULER_SIZE))
        meter_row.addWidget(self.h_meter_ruler)
        main_layout.addLayout(meter_row)

        # Row 2: corner + horizontal lat/lon ruler
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)
        top_layout.addWidget(_corner(AxisRuler.RULER_SIZE))
        top_layout.addWidget(self.h_ruler)
        main_layout.addLayout(top_layout)

        # Row 3: vertical metre ruler + vertical lat/lon ruler + canvas
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)
        bottom_layout.addWidget(self.v_meter_ruler)
        bottom_layout.addWidget(self.v_ruler)
        bottom_layout.addWidget(canvas)
        main_layout.addLayout(bottom_layout)

        # Connect to canvas view changes to update rulers
        self._connect_view_updates()

        # A rotated view (image-up cycle mode) breaks the lat/lon rulers: a
        # constant longitude is no longer a vertical screen line, so they blank
        # themselves out (see AxisRuler._view_is_rotated). Repaint on change.
        canvas.view_rotation_changed.connect(lambda _deg: self._update_rulers())

    def _connect_view_updates(self):
        """Connect to canvas signals to update rulers on view change."""
        # Override the canvas methods to trigger ruler updates
        original_wheel = self.canvas.wheelEvent
        original_scroll = self.canvas.scrollContentsBy
        original_resize = self.canvas.resizeEvent

        def wheel_wrapper(event):
            """Run the canvas wheel handler, then refresh the rulers."""
            original_wheel(event)
            self._update_rulers()

        def scroll_wrapper(dx, dy):
            """Run the canvas scroll handler, then refresh the rulers."""
            original_scroll(dx, dy)
            self._update_rulers()

        def resize_wrapper(event):
            """Run the canvas resize handler, then refresh the rulers."""
            original_resize(event)
            self._update_rulers()

        self.canvas.wheelEvent = wheel_wrapper
        self.canvas.scrollContentsBy = scroll_wrapper
        self.canvas.resizeEvent = resize_wrapper

    def _update_rulers(self):
        """Repaint the lat/lon and metre rulers."""
        self.h_ruler.update()
        self.v_ruler.update()
        self.h_meter_ruler.update()
        self.v_meter_ruler.update()
