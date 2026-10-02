"""The review section: has somebody checked this label, and what did they find?

Asked for 2026-09-28 as a quality-control pass over the snippets. Each
label is unreviewed until someone marks it accepted, rejected, or to be
looked at again. The worklist is "Needs review" - unreviewed or flagged
for another look - against "Reviewed".

Rejected labels stay in the project and the ground truth file, status and
all, so a rejection can be reversed; the HDF5 and sub-image exports leave
them out, and the canvas draws them dimmed.

In the Single view, A accepts, X rejects and F flags for another look;
with "Advance after marking" on (the default) the editor moves to the next
snippet, so a review pass needs no mouse. Each mark is one undo step.

In the Grid view's Review mode a page is marked at a time: the grid
(orientation_section.py) takes the clicks and reports them, and this
section sends them on - a page accepted at once as one undo step.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QGridLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from ..labels import (REVIEW_ACCEPTED, REVIEW_RECHECK, REVIEW_REJECTED,
                      REVIEW_UNREVIEWED, valid_review)
from .section import Section
from .strip import Worklist


def _status(entry: dict) -> str:
    return valid_review(entry.get("review"))


BADGES = {REVIEW_ACCEPTED: "  [\N{CHECK MARK}]",
          REVIEW_REJECTED: "  [\N{BALLOT X}]",
          REVIEW_RECHECK: "  [?]"}

REVIEW_WORKLIST = Worklist(
    needs="Needs review", done="Reviewed",
    is_done=lambda entry: _status(entry) in (REVIEW_ACCEPTED,
                                             REVIEW_REJECTED),
    badge=lambda entry: BADGES.get(_status(entry), ""),
    nothing_left="Every snippet here is reviewed. Nothing left to do.",
    none_done="No snippet here is reviewed yet.")

# Qt key -> status.
KEYS = {Qt.Key_A: REVIEW_ACCEPTED,
        Qt.Key_X: REVIEW_REJECTED,
        Qt.Key_F: REVIEW_RECHECK}

NAMES = {REVIEW_ACCEPTED: "Accepted", REVIEW_REJECTED: "Rejected",
         REVIEW_RECHECK: "Look again", REVIEW_UNREVIEWED: "Not reviewed"}


class ReviewPanel(QWidget):
    """Accept, Reject, Look again and Clear for the snippet in hand."""

    marked = pyqtSignal(str)             # a status; "" clears

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.state = QLabel(NAMES[REVIEW_UNREVIEWED])
        layout.addWidget(self.state)
        grid = QGridLayout()
        self.buttons = {}
        for column, (status, text, key, tip) in enumerate((
                # No mnemonics: A, X and F (which also advance) are the
                # keys, and an Alt+A that marked without advancing would
                # be a second, undocumented way.
                (REVIEW_ACCEPTED, "Accept", "A",
                 "The label is right."),
                (REVIEW_REJECTED, "Reject", "X",
                 "The label is wrong: kept in the project, left out of "
                 "the HDF5 and sub-image exports, drawn dimmed."),
                (REVIEW_RECHECK, "Look again", "F",
                 "Not sure: stays on the Needs review list."))):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setToolTip(f"{tip} (key {key})")
            button.clicked.connect(
                lambda _c=False, s=status: self.marked.emit(s))
            grid.addWidget(button, 0, column)
            self.buttons[status] = button
        layout.addLayout(grid)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Back to not reviewed.")
        self.clear_button.clicked.connect(
            lambda: self.marked.emit(REVIEW_UNREVIEWED))
        layout.addWidget(self.clear_button)
        self.advance_check = QCheckBox("Advance after marking")
        self.advance_check.setToolTip(
            "After A, X or F, move to the next snippet - a review pass "
            "without the mouse.")
        self.advance_check.setChecked(True)
        layout.addWidget(self.advance_check)
        hint = QLabel("Keys: A accept, X reject, F look again.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
        self._status = REVIEW_UNREVIEWED

    def status(self) -> str:
        return self._status

    def show_status(self, status: str, enabled: bool = True):
        self._status = status
        self.state.setText(NAMES.get(status, NAMES[REVIEW_UNREVIEWED]))
        for value, button in self.buttons.items():
            button.setChecked(value == status)
            button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled and status != REVIEW_UNREVIEWED)


class ReviewSection(Section):
    """The Snippet Editor's Review section."""

    key = "review"
    title = "Review"

    # (label_id, status) - out to the main window.
    review_changed = pyqtSignal(int, str)
    # ([label ids], status): a page marked at once - one undo step.
    reviews_changed = pyqtSignal(list, str)

    def __init__(self, grid=None, parent=None):
        """``grid`` is the Grid view, whose Review mode this section
        answers for (None: a section on its own)."""
        super().__init__(REVIEW_WORKLIST, ReviewPanel(), parent)
        self._entry = None
        self._grid = grid
        self.panel.marked.connect(self.mark)
        if grid is not None:
            grid.review_marked.connect(self._on_grid_marked)
            grid.reviews_marked.connect(self._on_grid_page_marked)
        self.show_entry(None)

    def _on_grid_marked(self, label_id: int, status: str):
        """The grid marked a snippet (and its entry): send it on."""
        self.review_changed.emit(label_id, status)
        self.entry_changed.emit(label_id)

    def _on_grid_page_marked(self, label_ids: list, status: str):
        self.reviews_changed.emit(label_ids, status)
        self.entries_changed.emit(label_ids)

    def set_shown(self, shown):
        super().set_shown(shown)
        if self._grid is not None:
            self._grid.set_review_shown(shown)

    def show_entry(self, entry):
        self._entry = entry
        self.panel.show_status(REVIEW_UNREVIEWED if entry is None
                               else _status(entry),
                               enabled=entry is not None)

    def refresh(self, label_id):
        if self._entry is not None and self._entry["label_id"] == label_id:
            self.show_entry(self._entry)
        if self._grid is not None:
            self._grid.refresh_review(label_id)

    def mark(self, status: str):
        """Mark the snippet in hand ("" clears)."""
        if self._entry is None or valid_review(status) != status:
            return
        self._entry["review"] = status
        self.show_entry(self._entry)
        label_id = self._entry["label_id"]
        self.review_changed.emit(label_id, status)
        self.entry_changed.emit(label_id)

    def view_changed(self, single):
        self.panel.setEnabled(single)
        return ("" if single else "In the Grid view, pick Review above the "
                                  "snippets to mark a page at a time.")

    def key_pressed(self, key):
        if key not in KEYS:
            return False
        self.mark(KEYS[key])
        if self.panel.advance_check.isChecked():
            self.advance_requested.emit()
        return True
