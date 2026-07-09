"""Central debug logging.

Timestamped debug messages are printed to stdout and, when the Debug Console is
open (Help -> Debug Console), shown live in a window. Call ``debug("...")`` from
anywhere; it is safe from background threads (the console updates on the UI
thread via a queued signal).

Format:  ``[YYYY-MM-DD HH:MM:SSZ DEBUG]: <message>``
"""
from collections import deque
from datetime import datetime, timezone

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)


class _DebugLog(QObject):
    """Singleton that formats, records and broadcasts debug messages."""

    # Emitted with each fully-formatted line so an open console can display it.
    message = pyqtSignal(str)

    # Bounded history so a console opened later still shows recent messages.
    _HISTORY_MAX = 2000

    def __init__(self):
        """Initialize the empty message history."""
        super().__init__()
        self._history: deque[str] = deque(maxlen=self._HISTORY_MAX)

    def log(self, text: str):
        """Format, print and broadcast a debug message."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}Z DEBUG]: {text}"
        self._history.append(line)
        print(line)
        try:
            self.message.emit(line)
        except RuntimeError:
            pass  # receiver torn down during shutdown

    def history(self) -> list[str]:
        """Return a copy of the recent message history (oldest first)."""
        return list(self._history)


_instance: "_DebugLog | None" = None


def debug_log() -> _DebugLog:
    """Return the process-wide debug logger, creating it on first use.

    Call this once on the main (UI) thread - e.g. during window init - before
    any background thread logs, so the QObject's thread affinity is the UI
    thread and cross-thread signal delivery is queued correctly.
    """
    global _instance
    if _instance is None:
        _instance = _DebugLog()
    return _instance


def debug(text: str):
    """Log a debug message (stdout + Debug Console)."""
    debug_log().log(text)


class DebugConsole(QDialog):
    """Non-modal window that displays live debug messages."""

    def __init__(self, parent=None):
        """Build the console, preload history and follow live messages."""
        super().__init__(parent)
        self.setWindowTitle("Debug Console")
        self.resize(760, 420)
        self.setWindowModality(Qt.NonModal)
        self._build_ui()

        log = debug_log()
        if log.history():
            self._view.setPlainText("\n".join(log.history()))
            self._scroll_to_end()
        log.message.connect(self._append)

    def _build_ui(self):
        """Assemble the read-only log view and its controls."""
        layout = QVBoxLayout(self)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(5000)  # cap memory for long sessions
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self._view.setFont(mono)
        layout.addWidget(self._view)

        row = QHBoxLayout()
        self._autoscroll = QCheckBox("Auto-scroll")
        self._autoscroll.setChecked(True)
        row.addWidget(self._autoscroll)
        row.addStretch(1)
        copy_btn = QPushButton("Copy all")
        copy_btn.clicked.connect(self._copy_all)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._view.clear)
        row.addWidget(copy_btn)
        row.addWidget(clear_btn)
        layout.addLayout(row)

    def _append(self, line: str):
        """Append a new line, respecting the auto-scroll toggle."""
        self._view.appendPlainText(line)
        if self._autoscroll.isChecked():
            self._scroll_to_end()

    def _scroll_to_end(self):
        """Scroll the log view to the newest line."""
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _copy_all(self):
        """Copy the entire visible log to the clipboard."""
        QApplication.clipboard().setText(self._view.toPlainText())
