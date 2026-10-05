"""The Display Settings window: how one group's imagery is drawn.

Opened from View > Display Settings... or a group's right-click menu. It
edits one group's DisplaySettings (app/display_settings.py): one band as
grayscale or a band for each of red, green and blue, and brightness,
contrast and gamma per channel, each over a histogram of that band; and,
for 16-bit and floating-point imagery, which values are drawn black and
white - a percentile window of each band, or one fixed range of values
(0 to 1 for imagery that is already a fraction).

Every change is sent out as it happens (``changed``), a moment after the
last one, so the canvas and the snippets follow the sliders. OK keeps what
is showing; Cancel, or closing the window, puts back what was there when it
opened. Display only: exports read the raw bands.
"""
import math

import numpy as np
from PyQt5.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                             QDialog, QDoubleSpinBox, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QPushButton, QRadioButton, QSizePolicy,
                             QSlider, QSpinBox, QVBoxLayout, QWidget)

from .display_settings import (BRIGHTNESS_RANGE, CONTRAST_RANGE, DEFAULT,
                               DEFAULT_PERCENTILES, GAMMA_RANGE, MODE_RGB,
                               MODE_SINGLE, STRETCH_PERCENTILE,
                               STRETCH_RANGE, ChannelAdjust,
                               DisplaySettings, Stretch, auto_adjust)

CHANNEL_NAMES = ("Red", "Green", "Blue")
CHANNEL_COLOURS = ("#c0392b", "#27ae60", "#2e6fd1")
GRAY_COLOUR = "#606060"
# The gamma slider is logarithmic: -100..100 -> 0.2..5.0, 0 -> 1.0.
GAMMA_BASE = GAMMA_RANGE[1]
EMIT_DELAY_MS = 40


def _gamma_to_slider(gamma: float) -> int:
    return int(round(100.0 * math.log(max(gamma, 1e-6), GAMMA_BASE)))


def _slider_to_gamma(value: int) -> float:
    return round(GAMMA_BASE ** (value / 100.0), 2)


class HistogramView(QWidget):
    """A band's display values as a filled curve, log-scaled so a dim
    picture on a dark background still shows its shape, with the channel's
    current lookup table drawn over it."""

    def __init__(self, colour: str, parent=None):
        super().__init__(parent)
        self._colour = QColor(colour)
        self._counts = None
        self._table = None
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_counts(self, counts):
        self._counts = None if counts is None else np.asarray(counts, float)
        self.update()

    def set_table(self, table):
        self._table = table
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(rect, self.palette().base())
        painter.setPen(self.palette().mid().color())
        painter.drawRect(rect)
        if self._counts is not None and self._counts.sum() > 0:
            heights = np.log1p(self._counts)
            heights /= heights.max() or 1.0
            path = QPainterPath()
            path.moveTo(rect.left(), rect.bottom())
            for value in range(256):
                x = rect.left() + rect.width() * value / 255.0
                path.lineTo(x, rect.bottom() - heights[value] * rect.height())
            path.lineTo(rect.right(), rect.bottom())
            path.closeSubpath()
            fill = QColor(self._colour)
            fill.setAlpha(110)
            painter.fillPath(path, fill)
        elif self._counts is None:
            painter.setPen(self.palette().mid().color())
            painter.drawText(rect, Qt.AlignCenter, "No image loaded")
        if self._table is not None:
            painter.setPen(self.palette().text().color())
            path = QPainterPath()
            for value in range(256):
                x = rect.left() + rect.width() * value / 255.0
                y = rect.bottom() - rect.height() * self._table[value] / 255.0
                if value == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.drawPath(path)
        painter.end()


class ChannelPanel(QGroupBox):
    """Brightness, contrast and gamma for one displayed channel."""

    changed = pyqtSignal()
    auto_requested = pyqtSignal()

    def __init__(self, title: str, colour: str, parent=None):
        super().__init__(title, parent)
        self.histogram = HistogramView(colour)
        self._counts = None

        self.brightness = QSpinBox()
        self.brightness.setRange(*BRIGHTNESS_RANGE)
        self.contrast = QSpinBox()
        self.contrast.setRange(*CONTRAST_RANGE)
        self.gamma = QDoubleSpinBox()
        self.gamma.setRange(*GAMMA_RANGE)
        self.gamma.setSingleStep(0.05)
        self.gamma.setDecimals(2)
        self.gamma.setValue(1.0)

        self.brightness_slider = self._slider(*BRIGHTNESS_RANGE)
        self.contrast_slider = self._slider(*CONTRAST_RANGE)
        self.gamma_slider = self._slider(-100, 100)

        self._link(self.brightness_slider, self.brightness)
        self._link(self.contrast_slider, self.contrast)
        self.gamma_slider.valueChanged.connect(
            lambda v: self._set_quietly(self.gamma, _slider_to_gamma(v)))
        self.gamma.valueChanged.connect(
            lambda v: self._set_quietly(self.gamma_slider,
                                        _gamma_to_slider(v)))
        for box in (self.brightness, self.contrast, self.gamma):
            box.valueChanged.connect(self._on_changed)

        self.hide_check = QCheckBox("Hide this channel")
        self.hide_check.setToolTip(
            "Draw this channel as black, exactly - its band leaves the "
            "picture. The sliders are kept for when it comes back.")
        self.hide_check.toggled.connect(self._on_hidden)

        self.auto_button = QPushButton("Auto")
        self.auto_button.setToolTip(
            "Spread this band's 2-98 percentile range of values over the "
            "full range from black to white")
        self.auto_button.clicked.connect(self.auto_requested)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setToolTip("Brightness 0, contrast 0, gamma 1")
        self.reset_button.clicked.connect(
            lambda: self.set_adjust(ChannelAdjust(), emit=True))

        form = QFormLayout()
        form.addRow("Brightness", self._row(self.brightness_slider,
                                            self.brightness))
        form.addRow("Contrast", self._row(self.contrast_slider, self.contrast))
        form.addRow("Gamma", self._row(self.gamma_slider, self.gamma))
        buttons = QHBoxLayout()
        buttons.addWidget(self.hide_check)
        buttons.addStretch(1)
        buttons.addWidget(self.auto_button)
        buttons.addWidget(self.reset_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.histogram)
        layout.addLayout(form)
        layout.addLayout(buttons)

    @staticmethod
    def _slider(low, high):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.setValue(0)
        return slider

    @staticmethod
    def _row(slider, box):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 1)
        box.setFixedWidth(72)
        layout.addWidget(box)
        return row

    def _link(self, slider, box):
        slider.valueChanged.connect(lambda v: self._set_quietly(box, v))
        box.valueChanged.connect(lambda v: self._set_quietly(slider, v))

    @staticmethod
    def _set_quietly(widget, value):
        """Mirror a value into the partner widget. Only the spin boxes
        announce changes, so a slider drag reaches _on_changed once."""
        if widget.value() == value:
            return
        if isinstance(widget, QSlider):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        else:
            widget.setValue(value)

    def adjust(self) -> ChannelAdjust:
        return ChannelAdjust(self.brightness.value(), self.contrast.value(),
                             round(self.gamma.value(), 2),
                             not self.hide_check.isHidden()
                             and self.hide_check.isChecked())

    def set_hideable(self, hideable: bool):
        """Single mode has one channel; hiding it would blank the image."""
        self.hide_check.setVisible(hideable)
        if not hideable and self.hide_check.isChecked():
            self.hide_check.setChecked(False)

    def _on_hidden(self, hidden: bool):
        for widget in (self.brightness, self.contrast, self.gamma,
                       self.brightness_slider, self.contrast_slider,
                       self.gamma_slider, self.auto_button):
            widget.setEnabled(not hidden)
        self._on_changed()

    def set_adjust(self, adjust: ChannelAdjust, emit: bool = False):
        self.hide_check.blockSignals(True)
        self.hide_check.setChecked(bool(adjust.hidden))
        self.hide_check.blockSignals(False)
        for widget in (self.brightness, self.contrast, self.gamma,
                       self.brightness_slider, self.contrast_slider,
                       self.gamma_slider, self.auto_button):
            widget.setEnabled(not adjust.hidden)
        boxes = (self.brightness, self.contrast, self.gamma)
        for box in boxes:
            box.blockSignals(True)
        self.brightness.setValue(int(adjust.brightness))
        self.contrast.setValue(int(adjust.contrast))
        self.gamma.setValue(float(adjust.gamma))
        for box in boxes:
            box.blockSignals(False)
        for slider, value in ((self.brightness_slider, adjust.brightness),
                              (self.contrast_slider, adjust.contrast),
                              (self.gamma_slider,
                               _gamma_to_slider(adjust.gamma))):
            self._set_quietly(slider, int(value))
        self.histogram.set_table(adjust.table())
        if emit:
            self.changed.emit()

    def set_counts(self, counts):
        self._counts = counts
        self.histogram.set_counts(counts)

    def counts(self):
        return self._counts

    def _on_changed(self, *_args):
        self.histogram.set_table(self.adjust().table())
        self.changed.emit()


class DisplaySettingsDialog(QDialog):
    """Edit one group's display settings, live.

    ``band_count`` is how many bands the group's imagery has (the band
    pickers' range); ``histogram_for(band, stretch)`` returns 256 counts
    of that band's display values through that stretch, from one of the
    group's images, or None when there is none to read; ``values`` (a
    SampledValues) says what that image holds, for the stretch controls
    to describe and suggest from - None when nothing could be read.
    """

    changed = pyqtSignal(object)      # DisplaySettings, while editing

    def __init__(self, group_path: str, settings: DisplaySettings,
                 band_count: int, histogram_for=None, parent=None,
                 values=None):
        super().__init__(parent)
        self.setWindowTitle(f"Display Settings - {group_path}")
        self.group_path = group_path
        self.original = settings
        self.band_count = max(1, int(band_count))
        self.values = values
        self._histogram_for = histogram_for
        self._histograms: dict = {}
        self._stretch = settings.stretch
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.setInterval(EMIT_DELAY_MS)
        self._emit_timer.timeout.connect(
            lambda: self.changed.emit(self.settings()))
        self._loading = False

        note = QLabel(
            f"Group <b>{group_path}</b> and its sub-groups, unless one has "
            "its own settings. Changes show as you make them.<br>Display "
            "only: exports keep the raw pixels.")
        note.setWordWrap(True)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("One band as grayscale", MODE_SINGLE)
        self.mode_combo.addItem("A band for red, green and blue", MODE_RGB)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.band_spin = self._band_spin()
        self.rgb_spins = [self._band_spin() for _ in range(3)]
        bands = QHBoxLayout()
        self.band_label = QLabel("Band")
        bands.addWidget(self.band_label)
        bands.addWidget(self.band_spin)
        self.rgb_labels = []
        for name, spin in zip(CHANNEL_NAMES, self.rgb_spins):
            label = QLabel(name)
            self.rgb_labels.append(label)
            bands.addWidget(label)
            bands.addWidget(spin)
        bands.addStretch(1)
        bands.addWidget(QLabel(f"of {self.band_count}"))

        top = QFormLayout()
        top.addRow("Show", self.mode_combo)
        top.addRow("Bands", bands)

        # The stretch: which values are drawn black and white, for
        # imagery that is not 8-bit already (that is drawn as stored).
        self.stretch_box = QGroupBox("Values drawn black to white")
        self.percentile_radio = QRadioButton("Percentiles")
        self.percentile_radio.setToolTip(
            "Each band's values between these percentiles of its own run "
            "from black to white; what lies outside clips. 2 to 98 is the "
            "default: the darkest 2% black, the brightest 2% white.")
        self.pct_low = self._stretch_spin(0.0, 99.9, 1, 0.5)
        self.pct_high = self._stretch_spin(0.1, 100.0, 1, 0.5)
        self.range_radio = QRadioButton("Values")
        self.range_radio.setToolTip(
            "These values are drawn black and white, the same for every "
            "band; what lies outside clips. For imagery that is already a "
            "fraction, 0 to 1 draws it as stored: times 255.")
        self.range_low = self._stretch_spin(-1e12, 1e12, 4, None)
        self.range_high = self._stretch_spin(-1e12, 1e12, 4, None)
        self.sampled_button = QPushButton("Sampled range")
        self.sampled_button.setToolTip(
            "The lowest and highest values sampled from the group's "
            "imagery - or 0 and 1 when they all lie within it.")
        self.sampled_button.clicked.connect(self._use_sampled_range)
        self.values_label = QLabel()
        self.values_label.setWordWrap(True)
        grid = QGridLayout(self.stretch_box)
        grid.addWidget(self.percentile_radio, 0, 0)
        grid.addWidget(self.pct_low, 0, 1)
        grid.addWidget(QLabel("to"), 0, 2)
        grid.addWidget(self.pct_high, 0, 3)
        grid.addWidget(QLabel("of each band's values"), 0, 4)
        grid.addWidget(self.range_radio, 1, 0)
        grid.addWidget(self.range_low, 1, 1)
        grid.addWidget(QLabel("to"), 1, 2)
        grid.addWidget(self.range_high, 1, 3)
        grid.addWidget(QLabel("for every band"), 1, 4)
        grid.addWidget(self.sampled_button, 1, 5)
        grid.setColumnStretch(6, 1)
        grid.addWidget(self.values_label, 2, 0, 1, 7)
        # One radio's toggle is enough: when either is picked the other
        # has already been put out, so the controls read consistently.
        self.range_radio.toggled.connect(self._on_stretch_changed)
        for spin in (self.pct_low, self.pct_high, self.range_low,
                     self.range_high):
            spin.valueChanged.connect(self._on_stretch_changed)

        self.panels = [ChannelPanel(name, colour)
                       for name, colour in zip(CHANNEL_NAMES,
                                               CHANNEL_COLOURS)]
        panels = QHBoxLayout()
        for index, panel in enumerate(self.panels):
            panel.changed.connect(self._schedule_emit)
            panel.auto_requested.connect(
                lambda i=index: self._auto(i))
            panels.addWidget(panel)

        self.auto_all_button = QPushButton("Auto All")
        self.auto_all_button.clicked.connect(self._auto_all)
        self.reset_button = QPushButton("Reset All")
        self.reset_button.setToolTip(
            "Back to how the app draws imagery by default")
        self.reset_button.clicked.connect(lambda: self.set_settings(
            DEFAULT, emit=True))
        self.ok_button = QPushButton("OK")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.auto_all_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(top)
        layout.addWidget(self.stretch_box)
        layout.addLayout(panels)
        layout.addLayout(buttons)

        self.set_settings(settings)

    def _band_spin(self):
        spin = QSpinBox()
        spin.setRange(1, self.band_count)
        spin.valueChanged.connect(self._on_bands_changed)
        return spin

    @staticmethod
    def _stretch_spin(low, high, decimals, step):
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        if step is None:
            # Values of any magnitude: a step to suit the number shown.
            spin.setStepType(QAbstractSpinBox.AdaptiveDecimalStepType)
        else:
            spin.setSingleStep(step)
        # A change here re-reads the imagery; a half-typed number is not
        # one. Enter, an arrow or leaving the box is.
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(96)
        return spin

    # -- the settings in the controls -----------------------------------------

    def mode(self) -> str:
        return self.mode_combo.currentData()

    def settings(self) -> DisplaySettings:
        if self.mode() == MODE_SINGLE:
            return DisplaySettings(
                mode=MODE_SINGLE, band=self.band_spin.value(),
                channels=(self.panels[0].adjust(), ChannelAdjust(),
                          ChannelAdjust()), stretch=self.stretch())
        bands = tuple(spin.value() for spin in self.rgb_spins)
        default_bands = DEFAULT.source_bands(self.band_count)
        return DisplaySettings(
            mode=MODE_RGB,
            bands=None if bands == default_bands else bands,
            channels=tuple(panel.adjust() for panel in self.panels),
            stretch=self.stretch())

    def stretch(self) -> Stretch:
        """The stretch the controls say. A window that is no window yet -
        high not above low, mid-edit - keeps the last one that was."""
        if self.range_radio.isChecked():
            kind = STRETCH_RANGE
            low, high = self.range_low.value(), self.range_high.value()
        else:
            kind = STRETCH_PERCENTILE
            low, high = self.pct_low.value(), self.pct_high.value()
        if high > low:
            self._stretch = Stretch(kind, low, high).clamped()
        return self._stretch

    def set_settings(self, settings: DisplaySettings, emit: bool = False):
        self._loading = True
        try:
            self.mode_combo.setCurrentIndex(
                self.mode_combo.findData(settings.mode))
            chosen = settings.source_bands(self.band_count)
            self.band_spin.setValue(chosen[0])
            rgb = (DisplaySettings(bands=settings.bands).source_bands(
                self.band_count))
            for spin, band in zip(self.rgb_spins, rgb):
                spin.setValue(band)
            for panel, adjust in zip(self.panels, settings.channels):
                panel.set_adjust(adjust)
            self._set_stretch(settings.stretch)
        finally:
            self._loading = False
        self._show_mode()
        self._show_values()
        self._refresh_histograms()
        if emit:
            self._schedule_emit()

    def _set_stretch(self, stretch: Stretch):
        """Fill the stretch controls. The kind not in use shows what it
        would be if picked: the default percentiles, or the range the
        imagery suggests."""
        self._stretch = stretch
        widgets = (self.percentile_radio, self.range_radio, self.pct_low,
                   self.pct_high, self.range_low, self.range_high)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            if stretch.kind == STRETCH_RANGE:
                self.range_radio.setChecked(True)
                self.range_low.setValue(stretch.low)
                self.range_high.setValue(stretch.high)
                self.pct_low.setValue(DEFAULT_PERCENTILES[0])
                self.pct_high.setValue(DEFAULT_PERCENTILES[1])
            else:
                self.percentile_radio.setChecked(True)
                self.pct_low.setValue(stretch.low)
                self.pct_high.setValue(stretch.high)
                low, high = self._suggested_range()
                self.range_low.setValue(low)
                self.range_high.setValue(high)
        finally:
            for widget in widgets:
                widget.blockSignals(False)

    def _suggested_range(self) -> tuple:
        """The fixed range to offer: 0 to 1 for unit-range imagery, else
        what was sampled, else 0 to 1 for want of anything better."""
        if self.values is not None:
            return self.values.suggested_range()
        return (0.0, 1.0)

    def _use_sampled_range(self):
        low, high = self._suggested_range()
        for widget in (self.range_low, self.range_high, self.range_radio):
            widget.blockSignals(True)
        self.range_low.setValue(low)
        self.range_high.setValue(high)
        self.range_radio.setChecked(True)
        for widget in (self.range_low, self.range_high, self.range_radio):
            widget.blockSignals(False)
        self._on_stretch_changed()

    def _show_values(self):
        """What the group's imagery holds - and, for 8-bit imagery, that
        the stretch has no say in it."""
        if self.values is None:
            self.values_label.setText(
                "No image of this group could be read. The stretch applies "
                "to 16-bit and floating-point imagery.")
            editable = True
        else:
            self.values_label.setText(self.values.describe())
            editable = not self.values.is_bytes()
        for widget in (self.percentile_radio, self.range_radio,
                       self.pct_low, self.pct_high, self.range_low,
                       self.range_high, self.sampled_button):
            widget.setEnabled(editable)

    # -- reacting --------------------------------------------------------------

    def _on_mode_changed(self, *_args):
        if self._loading:
            return
        self._show_mode()
        self._refresh_histograms()
        self._schedule_emit()

    def _on_bands_changed(self, *_args):
        if self._loading:
            return
        self._refresh_histograms()
        self._schedule_emit()

    def _on_stretch_changed(self, *_args):
        if self._loading:
            return
        # The histograms are of display values, which the stretch makes.
        self._refresh_histograms()
        self._schedule_emit()

    def _show_mode(self):
        single = self.mode() == MODE_SINGLE
        # The row is already labelled "Bands"; one band needs no second name.
        self.band_label.setVisible(False)
        self.band_spin.setVisible(single)
        for label, spin in zip(self.rgb_labels, self.rgb_spins):
            label.setVisible(not single)
            spin.setVisible(not single)
        self.panels[0].setTitle("Gray" if single else CHANNEL_NAMES[0])
        self.panels[0].histogram._colour = QColor(
            GRAY_COLOUR if single else CHANNEL_COLOURS[0])
        for panel in self.panels[1:]:
            panel.setVisible(not single)
        self.panels[0].set_hideable(not single)

    def _channel_bands(self) -> list:
        if self.mode() == MODE_SINGLE:
            return [self.band_spin.value()]
        return [spin.value() for spin in self.rgb_spins]

    def histogram(self, band: int):
        key = (band, self.stretch())
        if key not in self._histograms:
            counts = None
            if self._histogram_for is not None:
                try:
                    counts = self._histogram_for(band, key[1])
                except Exception:   # noqa: BLE001 - no histogram, still usable
                    counts = None
            self._histograms[key] = counts
        return self._histograms[key]

    def _refresh_histograms(self):
        for panel, band in zip(self.panels, self._channel_bands()):
            panel.set_counts(self.histogram(band))

    def _auto(self, index: int):
        counts = self.panels[index].counts()
        # A hidden channel stays hidden: Auto All must not bring it back.
        if counts is None or self.panels[index].hide_check.isChecked():
            return
        self.panels[index].set_adjust(auto_adjust(counts), emit=True)

    def _auto_all(self):
        for index in range(len(self._channel_bands())):
            self._auto(index)

    def _schedule_emit(self):
        if not self._loading:
            self._emit_timer.start()

    def flush(self):
        """Send a pending change now (OK does, and tests do)."""
        if self._emit_timer.isActive():
            self._emit_timer.stop()
            self.changed.emit(self.settings())
