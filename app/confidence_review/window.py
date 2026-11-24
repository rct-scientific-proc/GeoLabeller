"""The suggested-ratings window: train, look, correct, train again or accept.

A window of its own, large, because the work is looking at many snippets
at once (Ryan, 2026-11-24: "a large isolated grid view where a human can
go in and make corrections quickly to multiple snippets, and then either
re-train or accept. Kind of like the model review training").

Train learns a model per class from the ratings people gave and suggests a
rating for every label without one. The grid shows those labels, the
highest suggested rating first and, within a rating, the ones the model is
least sure of first - where a correction teaches most. Pick snippets
(click, Shift-click, Ctrl-click, drag a box) and press 1-5: that rating is
yours, a correction. Train again learns from the corrections too.

Nothing here is in the project until it is accepted - so nothing here can
reach an export. Accept writes the ratings shown - corrections as ratings
given by hand, the rest marked as accepted suggestions - through the main
window, as one undo step. Closing with corrections not yet accepted asks
whether to keep them.

Keys, in the grid: 1-5 rate the picked snippets, 0 takes a correction
back, A accepts the picked, Ctrl+A picks the page.
"""
from __future__ import annotations

import math

import numpy as np
from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox,
                             QHBoxLayout, QHeaderView, QLabel, QListView,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QProgressBar, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .. import settings_scope
from ..labels import CONFIDENCE_MAX, CONFIDENCE_MIN
from ..model_review.learner import (BACKBONES, SMALL,
                                    available as learning_available,
                                    backbone_reason,
                                    checked as learning_checked)
from ..model_review.training import LearnThread
from ..snippets import SnippetLoader
from . import ratings
from .ratings import CHIP_PX, Trainer

PAGE = 200                      # snippets a page
ICON_PX = 128
WORK_WAIT_MS = 5000             # closing waits this long for the work
DEFAULT_ACCEPT = 0.80
_BACKBONE_KEY = "confidence_review/backbone"
_ACCEPT_KEY = "confidence_review/accept"
_GEOMETRY_KEY = "confidence_review/geometry"

# A tint per rating, 1 (low, red) to 5 (high, green), behind a snippet.
RATING_COLOURS = {1: QColor(214, 84, 84), 2: QColor(224, 148, 72),
                  3: QColor(214, 196, 80), 4: QColor(150, 196, 96),
                  5: QColor(84, 170, 110)}

ALL = "__all__"
YOURS = "__yours__"
LEAST_SURE, SUREST = "least", "surest"

_RATING_KEYS = {getattr(Qt, f"Key_{n}"): n
                for n in range(CONFIDENCE_MIN, CONFIDENCE_MAX + 1)}


def _pixmap(arr) -> QPixmap:
    """An (H, W, 3) uint8 picture as an icon-sized pixmap."""
    arr = np.ascontiguousarray(arr[:, :, :3])
    height, width = arr.shape[:2]
    image = QImage(arr.data, width, height, 3 * width,
                   QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image).scaled(
        ICON_PX, ICON_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class SuggestWindow(QWidget):
    """See the module."""

    # ({label id: rating}, [the ids whose rating is a correction]): for
    # the main window to write, which answers with accepted(ids).
    accept_requested = pyqtSignal(dict, list)
    # The window has closed: what it held is gone, and the main window
    # makes another the next time it is asked for.
    closed = pyqtSignal()

    def __init__(self, get_project, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Suggested Confidence Ratings")
        self._get_project = get_project
        self._learning = (None, "Looking for PyTorch...")
        self._probe = None
        self._working = None
        self._trainer: "Trainer | None" = None
        self._outcome = None
        self._note = ""
        self._views: dict = {}          # label id -> ratings.View
        self._suggestions: dict = {}    # label id -> ratings.Suggestion
        self._corrections: dict = {}    # label id -> rating, the user's
        self._shown: list = []          # label ids, filtered and ordered
        self._page = 0
        self._items: dict = {}          # label id -> item, on this page
        self._discard_on_close = False
        self._prepared = None           # what the training in hand runs on
        self.loader = SnippetLoader(self)
        self.loader.ready.connect(self._on_snippet)
        self._setup_ui()
        self._restore_settings()
        self._start_probe()
        self._show_state()

    # -- the widgets ----------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        train = QHBoxLayout()
        self.train_button = QPushButton("Train")
        self.train_button.setToolTip(
            "Learn, per class, from the ratings people gave, and suggest "
            "a rating for every label without one. Corrections made here "
            "are learned from too.")
        self.train_button.clicked.connect(self.train)
        train.addWidget(self.train_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_work)
        train.addWidget(self.stop_button)
        train.addWidget(QLabel("Model:"))
        self.backbone_combo = QComboBox()
        for key, text in BACKBONES:
            self.backbone_combo.addItem(text, key)
        self.backbone_combo.currentIndexChanged.connect(
            self._on_backbone_changed)
        train.addWidget(self.backbone_combo)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        train.addWidget(self.status, 1)
        layout.addLayout(train)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(8)
        layout.addWidget(self.bar)

        view = QHBoxLayout()
        view.addWidget(QLabel("Class:"))
        self.class_combo = QComboBox()
        self.class_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.class_combo.currentIndexChanged.connect(self._refilter)
        view.addWidget(self.class_combo)
        view.addWidget(QLabel("Show:"))
        self.show_combo = QComboBox()
        self.show_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.show_combo.currentIndexChanged.connect(self._refilter)
        view.addWidget(self.show_combo)
        view.addWidget(QLabel("Order:"))
        self.order_combo = QComboBox()
        self.order_combo.addItem("Least sure first", LEAST_SURE)
        self.order_combo.addItem("Surest first", SUREST)
        self.order_combo.setToolTip(
            "Within each rating. The least sure are where a correction "
            "teaches the model most.")
        self.order_combo.currentIndexChanged.connect(self._refilter)
        view.addWidget(self.order_combo)
        view.addStretch(1)
        self.prev_button = QPushButton("<")
        self.prev_button.setFixedWidth(32)
        self.prev_button.clicked.connect(lambda: self._turn_to(self._page - 1))
        view.addWidget(self.prev_button)
        self.page_label = QLabel("")
        view.addWidget(self.page_label)
        self.next_button = QPushButton(">")
        self.next_button.setFixedWidth(32)
        self.next_button.clicked.connect(lambda: self._turn_to(self._page + 1))
        view.addWidget(self.next_button)
        layout.addLayout(view)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setIconSize(QSize(ICON_PX, ICON_PX))
        self.grid.setGridSize(QSize(ICON_PX + 20, ICON_PX + 40))
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(4)
        self.grid.installEventFilter(self)
        self.grid.itemSelectionChanged.connect(self._show_counts)
        layout.addWidget(self.grid, 1)

        accept = QHBoxLayout()
        self.hint = QLabel("Pick snippets and press 1-5 to rate them "
                           "yourself; 0 takes that back.")
        self.hint.setStyleSheet("color: palette(mid);")
        accept.addWidget(self.hint, 1)
        self.accept_button = QPushButton("Accept picked (A)")
        self.accept_button.setToolTip(
            "Write the ratings of the picked snippets to the project.")
        self.accept_button.clicked.connect(self.accept_picked)
        accept.addWidget(self.accept_button)
        self.accept_page_button = QPushButton("Accept page")
        self.accept_page_button.clicked.connect(self.accept_page)
        accept.addWidget(self.accept_page_button)
        self.accept_sure_button = QPushButton("Accept all shown at or above")
        self.accept_sure_button.setToolTip(
            "Every suggestion the Class and Show boxes let through that "
            "the model is at least this sure of, on every page - and "
            "every correction of yours among them.")
        self.accept_sure_button.clicked.connect(self.accept_sure)
        accept.addWidget(self.accept_sure_button)
        self.accept_spin = QDoubleSpinBox()
        self.accept_spin.setRange(0.0, 1.0)
        self.accept_spin.setSingleStep(0.05)
        self.accept_spin.setDecimals(2)
        self.accept_spin.setValue(DEFAULT_ACCEPT)
        self.accept_spin.valueChanged.connect(self._show_counts)
        accept.addWidget(self.accept_spin)
        layout.addLayout(accept)

        self.counts = QLabel("")
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)

        self.report = QTableWidget(0, 8)
        self.report.setHorizontalHeaderLabels(
            ["Class", "Rated", "Ratings learned", "Held out", "Exact",
             "Within 1", "Suggested", "Note"])
        header = self.report.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        self.report.verticalHeader().setVisible(False)
        self.report.setEditTriggers(QTableWidget.NoEditTriggers)
        self.report.setSelectionMode(QAbstractItemView.NoSelection)
        self.report.setMaximumHeight(150)
        self.report.setToolTip(
            "From the labels held out of training: how often the model "
            "gave the rating the person had, and how often within one of "
            "it.")
        layout.addWidget(self.report)
        self.resize(1180, 860)

    # -- settings -------------------------------------------------------------

    def _restore_settings(self):
        settings = settings_scope.settings()
        backbone = str(settings.value(_BACKBONE_KEY, SMALL))
        index = max(0, self.backbone_combo.findData(backbone))
        self.backbone_combo.blockSignals(True)
        self.backbone_combo.setCurrentIndex(index)
        self.backbone_combo.blockSignals(False)
        try:
            self.accept_spin.setValue(
                float(settings.value(_ACCEPT_KEY, DEFAULT_ACCEPT)))
        except (TypeError, ValueError):
            pass
        geometry = settings.value(_GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def save_settings(self):
        settings = settings_scope.settings()
        settings.setValue(_BACKBONE_KEY, self.backbone_combo.currentData())
        settings.setValue(_ACCEPT_KEY, self.accept_spin.value())
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())

    # -- PyTorch, looked for without waiting ---------------------------------

    def _start_probe(self):
        known = learning_checked()
        if known is not None:
            self._learning = known
            return
        probe = LearnThread(lambda _progress, _stop: learning_available(),
                            self)
        probe.done.connect(self._on_probed)
        probe.finished.connect(self._on_probe_finished)
        probe.finished.connect(probe.deleteLater)
        self._probe = probe
        probe.start()

    def _on_probed(self, result):
        self._learning = tuple(result)
        self._show_state()

    def _on_probe_finished(self):
        self._probe = None

    # -- what the window says of itself ---------------------------------------

    def _show_state(self):
        device, text = self._learning
        working = self._working is not None
        why = backbone_reason(self.backbone_combo.currentData())
        self.train_button.setEnabled(device is not None and not working
                                     and why is None)
        self.train_button.setText(
            "Train again" if self._outcome is not None else "Train")
        self.stop_button.setVisible(working)
        self.backbone_combo.setEnabled(not working)
        if not working:
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.status.setText(self._note or why or text)
        self._show_counts()

    def _show_counts(self):
        have = bool(self._shown)
        picked = len(self.grid.selectedItems())
        self.accept_button.setEnabled(picked > 0)
        self.accept_page_button.setEnabled(have)
        self.accept_sure_button.setEnabled(have)
        pages = max(1, math.ceil(len(self._shown) / PAGE))
        self.page_label.setText(f"Page {self._page + 1} of {pages}")
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page < pages - 1)
        waiting = len(set(self._suggestions) | set(self._corrections))
        if not waiting:
            self.counts.setText(
                "No suggestions yet: Train to get them." if self._outcome
                is None else "Nothing left to accept.")
            return
        sure = len(self._sure_ids())
        text = (f"{waiting} label{'' if waiting == 1 else 's'} with a "
                f"suggested rating, {len(self._shown)} shown; "
                f"{len(self._corrections)} corrected by you; {sure} shown "
                f"at or above {self.accept_spin.value():.2f}")
        if picked:
            text += f"; {picked} picked"
        self.counts.setText(text + ".")

    # -- what is shown --------------------------------------------------------

    def rating_of(self, label_id) -> "int | None":
        """The rating shown for a label: the user's, else the model's."""
        if label_id in self._corrections:
            return self._corrections[label_id]
        suggestion = self._suggestions.get(label_id)
        return None if suggestion is None else suggestion.rating

    def _certainty(self, label_id) -> float:
        suggestion = self._suggestions.get(label_id)
        return 1.0 if suggestion is None else suggestion.certainty

    def _fill_filters(self):
        """The Class and Show boxes, for what there is now; what was
        chosen is kept while it is still there."""
        ids = set(self._suggestions) | set(self._corrections)
        classes = sorted({self._views[i].class_name for i in ids
                          if i in self._views})
        for combo, entries in (
                (self.class_combo,
                 [("All classes", ALL)] + [(c, c) for c in classes]),
                (self.show_combo,
                 [("Every rating", ALL)]
                 + [(f"Rated {r}", r) for r in range(CONFIDENCE_MAX,
                                                     CONFIDENCE_MIN - 1, -1)]
                 + [("Corrected by you", YOURS)])):
            chosen = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for text, data in entries:
                combo.addItem(text, data)
            combo.setCurrentIndex(max(0, combo.findData(chosen)))
            combo.blockSignals(False)

    def _passes(self, label_id) -> bool:
        view = self._views.get(label_id)
        if view is None:
            return False
        wanted = self.class_combo.currentData()
        if wanted not in (None, ALL) and view.class_name != wanted:
            return False
        show = self.show_combo.currentData()
        if show == YOURS:
            return label_id in self._corrections
        if show not in (None, ALL):
            return self.rating_of(label_id) == show
        return True

    def _refilter(self, *_args):
        """What the boxes let through, the highest rating first; within
        a rating by how sure the model is (a correction counts as sure)."""
        ids = [i for i in set(self._suggestions) | set(self._corrections)
               if self._passes(i)]
        least = self.order_combo.currentData() != SUREST
        ids.sort(key=lambda i: (
            -(self.rating_of(i) or 0),
            self._certainty(i) if least else -self._certainty(i), i))
        self._shown = ids
        self._turn_to(self._page)

    def _turn_to(self, page: int):
        pages = max(1, math.ceil(len(self._shown) / PAGE))
        self._page = min(max(0, page), pages - 1)
        self.loader.cancel_all()
        self.grid.clear()
        self._items = {}
        wanted = []
        blank = QPixmap(ICON_PX, ICON_PX)
        blank.fill(QColor(90, 90, 90))
        for label_id in self._shown[self._page * PAGE:(self._page + 1) * PAGE]:
            view = self._views[label_id]
            item = QListWidgetItem(QIcon(blank), "")
            item.setData(Qt.UserRole, label_id)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(item)
            self._items[label_id] = item
            self._dress(label_id)
            wanted.append((label_id, view.image_path, view.pixel_x,
                           view.pixel_y))
        if wanted:
            self.loader.request_many(wanted, CHIP_PX)
        self._show_counts()

    def _dress(self, label_id):
        """An item's caption, tint and tooltip, for the rating it shows."""
        item = self._items.get(label_id)
        if item is None:
            return
        rating = self.rating_of(label_id)
        suggestion = self._suggestions.get(label_id)
        view = self._views[label_id]
        if rating is None:
            return                      # neither rated nor suggested for
        if label_id in self._corrections:
            caption = f"{rating}  yours"
        else:
            caption = f"{rating}?  {suggestion.certainty:.2f}"
        item.setText(caption)
        colour = QColor(RATING_COLOURS.get(rating, QColor(120, 120, 120)))
        colour.setAlpha(150)
        item.setBackground(QBrush(colour))
        tip = f"Label #{label_id}, {view.class_name}"
        if suggestion is not None:
            tip += f"\nSuggested: {suggestion.describe()}"
        if label_id in self._corrections:
            tip += f"\nYour rating: {rating}"
        item.setToolTip(tip)

    def _on_snippet(self, key, arr):
        item = self._items.get(key)
        if item is not None and arr is not None:
            item.setIcon(QIcon(_pixmap(arr)))

    def shown_ids(self) -> list:
        """The label ids on the page, in order."""
        return [self.grid.item(row).data(Qt.UserRole)
                for row in range(self.grid.count())]

    def picked_ids(self) -> list:
        return [item.data(Qt.UserRole) for item in self.grid.selectedItems()]

    # -- corrections ----------------------------------------------------------

    def rate_picked(self, rating: int):
        """The picked snippets are given this rating by the user: a
        correction, learned from at the next training. They stay where
        they are on the page until it is turned or filtered again."""
        if not ratings.valid_rating(rating):
            return
        ids = self.picked_ids()
        for label_id in ids:
            self._corrections[label_id] = rating
            self._dress(label_id)
        if ids:
            self._show_counts()

    def clear_picked(self):
        """Take back the corrections among the picked snippets: each
        shows the model's suggestion again."""
        changed = bare = False
        for label_id in self.picked_ids():
            if self._corrections.pop(label_id, None) is not None:
                changed = True
                if label_id in self._suggestions:
                    self._dress(label_id)
                else:
                    # Rated by the user, never suggested for (its class
                    # not learned): with the rating gone, so is it.
                    self._views.pop(label_id, None)
                    bare = True
        if bare:
            self._refilter()
        elif changed:
            self._show_counts()

    def eventFilter(self, watched, event):
        if watched is self.grid and event.type() == QEvent.KeyPress \
                and not event.modifiers() & (Qt.ControlModifier
                                             | Qt.AltModifier):
            key = event.key()
            if key in _RATING_KEYS:
                self.rate_picked(_RATING_KEYS[key])
                return True
            if key == Qt.Key_0:
                self.clear_picked()
                return True
            if key == Qt.Key_A:
                self.accept_picked()
                return True
        return super().eventFilter(watched, event)

    # -- training -------------------------------------------------------------

    def _on_backbone_changed(self, *_args):
        self._trainer = None            # another model: nothing carried over
        self._show_state()

    def train(self):
        """Gather what there is to learn from - here, on the GUI thread -
        and train off it."""
        if self._working is not None:
            return
        device, _text = self._learning
        if device is None:
            return
        self.sync()
        try:
            prepared = ratings.prepare(self._get_project(),
                                       self._corrections)
        except ValueError as exc:
            self._note = str(exc)
            self._show_state()
            return
        backbone = self.backbone_combo.currentData()
        if self._trainer is None or self._trainer.backbone != backbone:
            self._trainer = Trainer(backbone=backbone, device=device)
        trainer = self._trainer
        self._start_work(
            f"Training on {prepared.n_examples()} rated labels",
            prepared,
            lambda progress, should_stop: trainer.run(prepared, progress,
                                                      should_stop))

    def _start_work(self, title: str, prepared, job):
        """Run the job on a thread of its own; a method of its own so
        tests can stand in for it."""
        self._prepared = prepared
        self._note = ""
        thread = LearnThread(job, self)
        thread.progress.connect(self._on_work_progress)
        thread.done.connect(self._on_work_done)
        thread.failed.connect(self._on_work_failed)
        thread.finished.connect(self._on_work_finished)
        self._working = thread
        self.status.setText(title + "...")
        self.bar.setRange(0, 0)
        self._show_state()
        thread.start()

    def stop_work(self):
        if self._working is not None:
            self._working.stop()
            self.status.setText("Stopping...")

    def _on_work_progress(self, text, done, total):
        self.status.setText(text)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        else:
            self.bar.setRange(0, 0)

    def _on_work_done(self, outcome):
        """The training is done: its suggestions replace the last ones;
        the corrections stay, with the suggestion each had."""
        prepared = self._prepared
        kept = {i: s for i, s in self._suggestions.items()
                if i in self._corrections}
        views = {i: v for i, v in self._views.items()
                 if i in self._corrections}
        views.update(prepared.views())
        self._views = views
        self._suggestions = {**kept, **outcome.suggestions}
        self._outcome = outcome
        self._note = outcome.summary()
        self._show_report(outcome)
        self._fill_filters()
        self._page = 0
        self._refilter()
        self._show_state()

    def _on_work_failed(self, text):
        self._note = text
        self._show_state()

    def _on_work_finished(self):
        thread, self._working = self._working, None
        if thread is not None:
            thread.deleteLater()
        self._show_state()

    def _show_report(self, outcome):
        rows = list(outcome.results.values())
        self.report.setRowCount(len(rows))
        for row, result in enumerate(rows):
            plan, report = result.plan, result.report
            note = result.failed or plan.reason() or ""
            if not note and plan.left_out:
                note = "; ".join(
                    f"rating {r}: {n} example{'' if n == 1 else 's'}, "
                    "not learned" for r, n in plan.left_out.items())
            if report is not None and report.held_out:
                exact = f"{report.accuracy:.0%}"
                near = f"{result.within_one():.0%}"
                held = str(report.held_out)
            else:
                exact = near = held = "-"
            learned = (", ".join(str(c) for c in report.classes)
                       if report is not None else "-")
            cells = [plan.class_name, str(plan.rated), learned, held, exact,
                     near, str(result.suggested) if report is not None
                     else "-", note]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if 0 < column < 7:
                    item.setTextAlignment(Qt.AlignCenter)
                self.report.setItem(row, column, item)

    # -- accepting ------------------------------------------------------------

    def sync(self):
        """Drop what no longer stands: a label removed, rated elsewhere,
        moved or given another class since it was suggested for."""
        project = self._get_project()
        gone = [i for i, view in self._views.items()
                if not ratings.still_open(project, view)]
        if not gone:
            return
        for label_id in gone:
            self._views.pop(label_id, None)
            self._suggestions.pop(label_id, None)
            self._corrections.pop(label_id, None)
        self._fill_filters()
        self._refilter()

    def _sure_ids(self) -> list:
        threshold = self.accept_spin.value()
        return [i for i in self._shown
                if i in self._corrections
                or self._certainty(i) >= threshold]

    def _request(self, ids):
        self.sync()
        chosen = {i: self.rating_of(i) for i in ids
                  if i in self._views and self.rating_of(i) is not None}
        if chosen:
            self.accept_requested.emit(
                chosen, [i for i in chosen if i in self._corrections])

    def accept_picked(self):
        self._request(self.picked_ids())

    def accept_page(self):
        self._request(self.shown_ids())

    def accept_sure(self):
        self._request(self._sure_ids())

    def accepted(self, label_ids):
        """The main window wrote these: they are done with here."""
        for label_id in label_ids:
            self._views.pop(label_id, None)
            self._suggestions.pop(label_id, None)
            self._corrections.pop(label_id, None)
        n = len(label_ids)
        self._note = (f"{n} rating{'' if n == 1 else 's'} written to the "
                      "project (Ctrl+Z in the main window undoes them).")
        self._fill_filters()
        self._refilter()
        self._show_state()

    # -- closing --------------------------------------------------------------

    def pending(self) -> int:
        """Corrections made and not accepted."""
        return len(self._corrections)

    def _ask_close(self) -> str:
        """"save", "discard" or "cancel" for corrections not accepted."""
        n = self.pending()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Suggested Confidence Ratings")
        box.setText(f"You rated {n} snippet{'' if n == 1 else 's'} here "
                    "that are not in the project yet.")
        box.setInformativeText("Write those ratings to the project?")
        box.setStandardButtons(
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Save)
        return {QMessageBox.Save: "save",
                QMessageBox.Discard: "discard"}.get(box.exec_(), "cancel")

    def discard_and_close(self):
        """Close without asking: the project is being replaced."""
        self._discard_on_close = True
        self.close()

    def closeEvent(self, event):
        if not self._discard_on_close and self.pending():
            choice = self._ask_close()
            if choice == "cancel":
                event.ignore()
                return
            if choice == "save":
                self._request(list(self._corrections))
        self.save_settings()
        self.loader.shutdown()
        if self._working is not None:
            self._working.stop()
            if not self._working.wait(WORK_WAIT_MS):
                # A QThread destroyed while running aborts the process:
                # it goes on alone, unheard, and deletes itself when done.
                worker = self._working
                for signal, slot in (
                        (worker.progress, self._on_work_progress),
                        (worker.done, self._on_work_done),
                        (worker.failed, self._on_work_failed),
                        (worker.finished, self._on_work_finished)):
                    signal.disconnect(slot)
                worker.setParent(None)
                worker.finished.connect(worker.deleteLater)
                self._working = None
                self._trainer = None    # its models are still in use
        if self._probe is not None and self._probe.isRunning():
            self._probe.setParent(None)     # an import cannot be stopped
            self._probe = None
        super().closeEvent(event)
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
