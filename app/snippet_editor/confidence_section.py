"""The confidence section: how sure the labeller is of each label, 1-5.

For the ML team, to know which snippets to train on and which matter less
to get exactly right: 1 is the lowest confidence, 5 the highest, and 0
means nobody has rated the label yet (which is also what the HDF5 export
writes for it). Five buttons and Clear rate the snippet in hand; in the
Single view the keys 1-5 do the same and 0 clears.

The smallest complete Section (section.py): a worklist, a panel, a key
handler - no overlay and no tool. The next per-snippet value can start
from a copy of this file.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..labels import (CONFIDENCE_MAX, CONFIDENCE_MIN, CONFIDENCE_UNSET,
                      valid_confidence)
from .section import Section
from .strip import Worklist


def _rating(entry: dict) -> int:
    return valid_confidence(entry.get("confidence"))


CONFIDENCE_WORKLIST = Worklist(
    needs="Needs confidence", done="Rated",
    is_done=lambda entry: _rating(entry) != CONFIDENCE_UNSET,
    badge=lambda entry: (f"  [{_rating(entry)}/{CONFIDENCE_MAX}]"
                         if _rating(entry) else ""),
    nothing_left="Every snippet here is rated. Nothing left to do.",
    none_done="No snippet here is rated yet.")

# Qt key -> rating: 1-5 rate, 0 clears.
_KEYS = {getattr(Qt, f"Key_{n}"): n
         for n in range(CONFIDENCE_UNSET, CONFIDENCE_MAX + 1)}


class ConfidencePanel(QWidget):
    """Five buttons and Clear for the snippet in hand."""

    rated = pyqtSignal(int)              # 0 (clear) to 5

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        question = QLabel("How sure are you of this label?  "
                          f"{CONFIDENCE_MIN} lowest, {CONFIDENCE_MAX} "
                          "highest.")
        question.setWordWrap(True)
        layout.addWidget(question)
        row = QHBoxLayout()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons = {}
        for value in range(CONFIDENCE_MIN, CONFIDENCE_MAX + 1):
            button = QPushButton(str(value))
            button.setCheckable(True)
            button.setMinimumWidth(28)
            button.setToolTip(f"Rate this label {value} of {CONFIDENCE_MAX} "
                              f"(key {value}).")
            button.clicked.connect(lambda _c=False, v=value: self.rated.emit(v))
            self._group.addButton(button, value)
            self.buttons[value] = button
            row.addWidget(button)
        layout.addLayout(row)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Leave this label unrated (key 0).")
        self.clear_button.clicked.connect(
            lambda: self.rated.emit(CONFIDENCE_UNSET))
        layout.addWidget(self.clear_button)
        hint = QLabel("Keys 1-5 rate the snippet in hand; 0 clears. "
                      "Unrated labels export as 0.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
        self._value = CONFIDENCE_UNSET

    def value(self) -> int:
        return self._value

    def show_value(self, value: int, enabled: bool = True):
        """Check the button for ``value`` (none for unrated)."""
        self._value = value
        # An exclusive group will not uncheck its last button on its own.
        self._group.setExclusive(False)
        for rating, button in self.buttons.items():
            button.setChecked(rating == value)
            button.setEnabled(enabled)
        self._group.setExclusive(True)
        self.clear_button.setEnabled(enabled and value != CONFIDENCE_UNSET)


class ConfidenceSection(Section):
    """The Snippet Editor's Confidence section."""

    key = "confidence"
    title = "Confidence"

    # (label_id, rating 0-5) - out to the main window.
    confidence_changed = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(CONFIDENCE_WORKLIST, ConfidencePanel(), parent)
        self._entry = None
        self.panel.rated.connect(self.rate)
        self.show_entry(None)

    def show_entry(self, entry):
        self._entry = entry
        self.panel.show_value(CONFIDENCE_UNSET if entry is None
                              else _rating(entry), enabled=entry is not None)

    def refresh(self, label_id):
        if self._entry is not None and self._entry["label_id"] == label_id:
            self.show_entry(self._entry)

    def rate(self, value: int):
        """Rate the snippet in hand (0 clears)."""
        if self._entry is None:
            return
        if value != CONFIDENCE_UNSET and valid_confidence(value) != value:
            return
        self._entry["confidence"] = value
        self.show_entry(self._entry)
        label_id = self._entry["label_id"]
        self.confidence_changed.emit(label_id, value)
        self.entry_changed.emit(label_id)

    def view_changed(self, single):
        # Rated on the snippet in hand, which the Grid view does not show.
        self.panel.setEnabled(single)
        return ("" if single else "Ratings are given in the Single view - "
                                  "double-click a snippet to open it there.")

    def key_pressed(self, key):
        if key not in _KEYS:
            return False
        self.rate(_KEYS[key])
        return True
