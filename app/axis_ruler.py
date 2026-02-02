"""Axis rulers for displaying lat/lon coordinates around the map canvas."""
import math
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt5.QtGui import QPainter, QPen, QFont, QFontMetrics
from PyQt5.QtCore import Qt


class AxisRuler(QWidget):
    """A ruler widget that displays coordinate tick marks."""

    RULER_SIZE = 30  # Width/height of the ruler in pixels

    def __init__(self, orientation: Qt.Orientation, canvas):
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

    def _draw_horizontal_ticks(self, painter: QPainter):
        """Draw longitude tick marks."""
        if not self.canvas._layers:
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
        if not self.canvas._layers:
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
        if range_val <= 0:
            return 1

        # Target roughly 5-10 ticks
        rough_interval = range_val / 7

        # Round to a nice number
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


class MapCanvasWithAxes(QWidget):
    """Widget that combines MapCanvas with axis rulers."""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas

        # Create rulers
        self.h_ruler = AxisRuler(Qt.Horizontal, canvas)
        self.v_ruler = AxisRuler(Qt.Vertical, canvas)

        # Corner widget (fills the corner between rulers)
        corner = QWidget()
        corner.setFixedSize(AxisRuler.RULER_SIZE, AxisRuler.RULER_SIZE)
        corner.setStyleSheet("background-color: white;")

        # Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top row: corner + horizontal ruler
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)
        top_layout.addWidget(corner)
        top_layout.addWidget(self.h_ruler)
        main_layout.addLayout(top_layout)

        # Bottom row: vertical ruler + canvas
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)
        bottom_layout.addWidget(self.v_ruler)
        bottom_layout.addWidget(canvas)
        main_layout.addLayout(bottom_layout)

        # Connect to canvas view changes to update rulers
        self._connect_view_updates()

    def _connect_view_updates(self):
        """Connect to canvas signals to update rulers on view change."""
        # Override the canvas methods to trigger ruler updates
        original_wheel = self.canvas.wheelEvent
        original_scroll = self.canvas.scrollContentsBy
        original_resize = self.canvas.resizeEvent

        def wheel_wrapper(event):
            original_wheel(event)
            self._update_rulers()

        def scroll_wrapper(dx, dy):
            original_scroll(dx, dy)
            self._update_rulers()

        def resize_wrapper(event):
            original_resize(event)
            self._update_rulers()

        self.canvas.wheelEvent = wheel_wrapper
        self.canvas.scrollContentsBy = scroll_wrapper
        self.canvas.resizeEvent = resize_wrapper

    def _update_rulers(self):
        """Update both rulers."""
        self.h_ruler.update()
        self.v_ruler.update()
