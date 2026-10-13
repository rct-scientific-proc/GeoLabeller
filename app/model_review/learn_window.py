"""The training window: what is happening, then what came of it.

Asked for 2026-10-13: a wrapping line in the Learn row was too little
to watch a training by. The window shows the phase (reading chips,
features, training by pass, suggesting), a progress bar, the time gone
and Stop; when the work is done, the report: a table per class (trained
on, held out, right, precision, recall), the confusion matrix, the loss
and held-out accuracy by pass, and how many chips a suggestion at or
above the Accept threshold would take, per class, so the threshold can
be chosen with the numbers in view.

The review window owns it, feeds it, and keeps it for Results...
"""
from __future__ import annotations

import math
import time
from collections import Counter

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (QHBoxLayout, QHeaderView, QLabel,
                             QProgressBar, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .learner import name_of

LOSS_COLOR = QColor(230, 120, 60)
VAL_LOSS_COLOR = QColor(60, 140, 230)
ACC_COLOR = QColor(90, 200, 110)
GRID_COLOR = QColor(70, 70, 70)
TEXT_COLOR = QColor(200, 200, 200)


def threshold_line(suggestions: dict, threshold: float) -> str:
    """"3,120 of 4,800 suggestions at or above 0.90: rock 1,900, Ignore
    1,220" - what Accept the page would take, over every page."""
    if not suggestions:
        return "No suggestions yet."
    taken = Counter(s.class_name for s in suggestions.values()
                    if s.confidence >= threshold)
    n = sum(taken.values())
    text = (f"{n:,} of {len(suggestions):,} suggestions at or above "
            f"{threshold:.2f}")
    if taken:
        text += ": " + ", ".join(f"{name_of(c)} {m:,}"
                                 for c, m in taken.most_common())
    return text + "."


class CurveView(QWidget):
    """Loss and held-out accuracy by pass."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list = []
        self.setMinimumHeight(140)

    def set_history(self, history):
        self._history = list(history or [])
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        painter.setRenderHint(QPainter.Antialiasing, True)
        margin = 28
        plot = QRectF(margin, 8, max(10, self.width() - margin - 8),
                      max(10, self.height() - 8 - 18))
        painter.setPen(QPen(GRID_COLOR, 1))
        painter.drawRect(plot)
        rows = [r for r in self._history if r.get("loss") is not None]
        if len(rows) < 2:
            painter.setPen(TEXT_COLOR)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "No training yet" if not rows
                             else "One pass")
            painter.end()
            return
        losses = [r["loss"] for r in rows] + [r["val_loss"] for r in rows
                                              if r.get("val_loss") is not None]
        top = max(losses) or 1.0
        n = len(rows)

        def point(i, value, scale):
            x = plot.left() + plot.width() * i / (n - 1)
            y = plot.bottom() - plot.height() * min(1.0, value / scale)
            return QPointF(x, y)

        for key, colour, scale in (("loss", LOSS_COLOR, top),
                                   ("val_loss", VAL_LOSS_COLOR, top),
                                   ("val_acc", ACC_COLOR, 1.0)):
            points = [point(i, r[key], scale) for i, r in enumerate(rows)
                      if r.get(key) is not None]
            if len(points) < 2:
                continue
            painter.setPen(QPen(colour, 2))
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)
        painter.setPen(TEXT_COLOR)
        painter.drawText(QRectF(0, plot.top() - 6, margin - 4, 14),
                         Qt.AlignRight, f"{top:.2f}")
        painter.drawText(QRectF(0, plot.bottom() - 8, margin - 4, 14),
                         Qt.AlignRight, "0")
        legend = (f"passes 1-{rows[-1]['pass']}: loss (orange), held-out "
                  "loss (blue), held-out accuracy (green, 0-1)")
        painter.drawText(QRectF(plot.left(), plot.bottom() + 2, plot.width(),
                                16), Qt.AlignLeft, legend)
        painter.end()


class LearnWindow(QWidget):
    """See the module."""

    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Model Review - training")
        self._started = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._suggestions: dict = {}
        self._threshold = 0.9
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.phase = QLabel("No training yet.")
        self.phase.setWordWrap(True)
        top.addWidget(self.phase, 1)
        self.elapsed = QLabel("")
        top.addWidget(self.elapsed)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_stop)
        top.addWidget(self.stop_button)
        layout.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)
        tables = QHBoxLayout()
        self.per_class = QTableWidget(0, 6)
        self.per_class.setHorizontalHeaderLabels(
            ["Class", "Trained on", "Held out", "Right", "Precision",
             "Recall"])
        self.per_class.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.per_class.verticalHeader().setVisible(False)
        self.per_class.setEditTriggers(QTableWidget.NoEditTriggers)
        tables.addWidget(self.per_class, 3)
        self.confusion = QTableWidget(0, 0)
        self.confusion.setEditTriggers(QTableWidget.NoEditTriggers)
        self.confusion.setToolTip("Rows: the true class of the held-out "
                                  "chips. Columns: what the model said.")
        tables.addWidget(self.confusion, 2)
        layout.addLayout(tables)
        self.curves = CurveView()
        layout.addWidget(self.curves, 1)
        self.threshold_label = QLabel(threshold_line({}, self._threshold))
        self.threshold_label.setWordWrap(True)
        layout.addWidget(self.threshold_label)
        self.resize(760, 620)

    # -- while it runs --------------------------------------------------------

    def start(self, title: str):
        self._started = time.time()
        self.phase.setText(title + "...")
        self.bar.setRange(0, 0)             # busy until a count comes
        self.elapsed.setText("0 s")
        self.stop_button.setEnabled(True)
        self._timer.start()

    def progress(self, text: str, done: int, total: int):
        self.phase.setText(text)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        else:
            self.bar.setRange(0, 0)

    def stopping(self):
        self.phase.setText("Stopping...")
        self.stop_button.setEnabled(False)

    def _on_stop(self):
        self.stopping()
        self.stop_requested.emit()

    def _tick(self):
        if self._started is not None:
            self.elapsed.setText(f"{time.time() - self._started:.0f} s")

    def _done_running(self):
        self._timer.stop()
        self._tick()
        self._started = None
        self.stop_button.setEnabled(False)

    # -- what came of it ------------------------------------------------------

    def finished(self, report, suggestions: dict, threshold: float,
                 text: "str | None" = None):
        """The work ended: the report (None for a save), the suggestions
        there are now, and the Accept threshold."""
        self._done_running()
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.phase.setText(text or "Done.")
        if report is not None:
            self.set_report(report)
        self.set_suggestions(suggestions, threshold)

    def failed(self, text: str):
        self._done_running()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.phase.setText(text)

    def set_report(self, report):
        self.summary.setText(report.summary())
        precision_recall = report.precision_recall()
        self.per_class.setRowCount(len(report.classes))
        for row, c in enumerate(report.classes):
            right, total = report.per_class.get(c, (0, 0))
            precision, recall = precision_recall.get(c, (math.nan, math.nan))
            cells = [name_of(c), str(report.counts.get(c, 0)), str(total),
                     str(right) if total else "-",
                     f"{precision:.0%}" if not math.isnan(precision) else "-",
                     f"{recall:.0%}" if not math.isnan(recall) else "-"]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignCenter)
                self.per_class.setItem(row, column, item)
        n = len(report.classes)
        self.confusion.setRowCount(n)
        self.confusion.setColumnCount(n)
        names = [name_of(c) for c in report.classes]
        self.confusion.setHorizontalHeaderLabels(names)
        self.confusion.setVerticalHeaderLabels(names)
        for r, true in enumerate(report.classes):
            for c, said in enumerate(report.classes):
                item = QTableWidgetItem(str(report.confusion.get((true, said),
                                                                 0)))
                item.setTextAlignment(Qt.AlignCenter)
                self.confusion.setItem(r, c, item)
        self.confusion.resizeColumnsToContents()
        self.curves.set_history(report.history)

    def set_suggestions(self, suggestions: dict, threshold: float):
        self._suggestions = dict(suggestions)
        self._threshold = float(threshold)
        self.threshold_label.setText(threshold_line(self._suggestions,
                                                    self._threshold))

    def set_threshold(self, threshold: float):
        self.set_suggestions(self._suggestions, threshold)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
