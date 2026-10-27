"""Labels > Find Candidates...: the detector's window.

Which images, which detector and its controls - each with the line
that says what raising it does, and the whole account in a panel - the
sizes in metres or pixels, the sensitivity, the budget of candidates
per image, the skips; Check against my labels, which fills a table
with what each sensitivity would have found of the project's own
labels; Run, which writes the candidates file beside the project and
hands it to Model Review. The work runs on a thread of its own with
progress and Stop; the window stays open, so a check leads to a run
and a run to another with the figures in view. Settings remembered.

The main window supplies the images for each choice and the project's
path, and opens the result; the dialog knows nothing of the canvas.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QPlainTextEdit, QProgressBar, QPushButton,
                             QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from ..model_review.ledger import DEFAULT_TOLERANCE_PX, LedgerIndex
from ..model_review.outcomes import read_outcomes
from ..settings_scope import settings as app_settings
from . import DETECTORS, by_key
from .evaluate import DEFAULT_SWEEP, check
from .run import (METRES, PIXELS, Settings, candidates_path, run,
                  write_candidates)

_SETTINGS_KEY = "detect/settings"
_IMAGES_KEY = "detect/images"
SELECTED, VISIBLE, EVERY = "selected", "visible", "all"
CLOSE_WAIT_MS = 5000            # closing waits this long for the work
IMAGE_CHOICES = ((SELECTED, "The selected group"),
                 (VISIBLE, "The images shown on the map"),
                 (EVERY, "Every image in the project"))

COMMON_DOC = (
    "Sensitivity - how far a place must stand out from its background, in "
    "spreads of that background (sigmas), to be a candidate. Raise it for "
    "fewer candidates, the surest only; lower it to catch fainter things "
    "at the price of more clutter. Check against my labels says what each "
    "setting would have found of what you have labelled already.\n\n"
    "Smallest and largest size - the objects you are after, across, in "
    "metres where the image is georeferenced (pixels where it is not: the "
    "same figures are then taken as pixels). Anything much smaller or "
    "larger is left out, and the image is read at a scale that suits the "
    "smallest, so a tighter range is a quicker run.\n\n"
    "Candidates per image at most - the budget: the strongest are kept. "
    "Zero keeps them all.\n\n"
    "Skip within a distance of a label - a candidate on an object you "
    "have labelled already teaches nothing; nor does one the last run's "
    "candidates file already holds an answer for - ignored, or imported "
    "- within Model Review's Same chip within distance (or half the "
    "candidate's size, if that is more), if Skip what was reviewed is "
    "on. The new file keeps those answers.\n\n"
    "Check against my labels counts a label as found when it lies within "
    "a candidate's own patch, or the smallest size, whichever is larger.")


def review_tolerance() -> float:
    """Model Review's Same chip within, as it stands."""
    try:
        return float(app_settings().value("model_review/tolerance",
                                          DEFAULT_TOLERANCE_PX))
    except (TypeError, ValueError):
        return float(DEFAULT_TOLERANCE_PX)


class Worker(QThread):
    """A job - a callable taking (progress, should_stop) - on a thread of
    its own, reporting progress as (text, done, total), then the result
    or why it failed. Stop asks the job to stop at its next look."""

    progress = pyqtSignal(str, int, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self._job = job
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.done.emit(self._job(self.progress.emit, lambda: self._stop))
        except Exception as exc:  # noqa: BLE001 - said in the window
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class DetectDialog(QDialog):
    """See the module. ``images_for(choice)`` returns the project images
    for one of IMAGE_CHOICES; ``project_getter()`` the project;
    ``path_getter()`` the project file's path, or None unsaved."""

    finished_run = pyqtSignal(str, object)     # the file written, the Report

    def __init__(self, project_getter, images_for, path_getter,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find Candidates")
        self.setModal(False)
        self._project = project_getter
        self._images_for = images_for
        self._path = path_getter
        self._worker: Worker | None = None
        self._closing = False           # a close cancels the work's result
        self._target = None             # where the run's file goes
        self._run_settings = None
        self._previous: list = []       # the last file's notes
        self._param_widgets: dict = {}
        self._build()
        self._restore()
        self._show_detector()

    # -- layout ---------------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.images_combo = QComboBox()
        for key, text in IMAGE_CHOICES:
            self.images_combo.addItem(text, key)
        form.addRow("Look at", self.images_combo)
        self.detector_combo = QComboBox()
        for detector in DETECTORS:
            self.detector_combo.addItem(detector.title, detector.key)
        self.detector_combo.currentIndexChanged.connect(
            lambda _i: self._show_detector())
        form.addRow("Detector", self.detector_combo)
        layout.addLayout(form)

        self.params_box = QGroupBox("The detector's controls")
        self.params_form = QFormLayout(self.params_box)
        layout.addWidget(self.params_box)

        common = QGroupBox("What to look for")
        grid = QFormLayout(common)
        self.sensitivity = QDoubleSpinBox()
        self.sensitivity.setRange(1.0, 30.0)
        self.sensitivity.setSingleStep(0.5)
        self.sensitivity.setDecimals(1)
        self.sensitivity.setSuffix(" sigma")
        self.sensitivity.setToolTip(
            "How far a place must stand out from its background to be a\n"
            "candidate. Raise it for fewer candidates, the surest only.")
        grid.addRow("Sensitivity", self.sensitivity)
        sizes = QHBoxLayout()
        self.smallest = QDoubleSpinBox()
        self.largest = QDoubleSpinBox()
        for spin in (self.smallest, self.largest):
            spin.setRange(0.1, 100000.0)
            spin.setDecimals(1)
            spin.setKeyboardTracking(False)
        self.smallest.setToolTip(
            "The smallest object you are after, across. Nothing much\n"
            "smaller is a candidate, and the image is read at a scale that\n"
            "suits this: larger is a quicker run.")
        self.largest.setToolTip(
            "The largest object you are after, across. Nothing much larger\n"
            "is a candidate.")
        self.unit = QComboBox()
        self.unit.addItem("metres", METRES)
        self.unit.addItem("pixels", PIXELS)
        self.unit.setToolTip(
            "Metres go by each image's georeferencing; an image without any\n"
            "takes the figures as pixels, and the run says so. Pixels apply\n"
            "to every image as they are.")
        sizes.addWidget(self.smallest)
        sizes.addWidget(QLabel("to"))
        sizes.addWidget(self.largest)
        sizes.addWidget(self.unit)
        grid.addRow("Object size", sizes)
        self.per_image = QSpinBox()
        self.per_image.setRange(0, 10000)
        self.per_image.setSpecialValueText("all")
        self.per_image.setToolTip(
            "The strongest this many candidates on each image, at most.\n"
            "0 keeps them all.")
        grid.addRow("Candidates per image at most", self.per_image)
        skip = QHBoxLayout()
        self.skip_labels = QDoubleSpinBox()
        self.skip_labels.setRange(0.0, 10000.0)
        self.skip_labels.setDecimals(0)
        self.skip_labels.setSuffix(" px")
        self.skip_labels.setToolTip(
            "No candidate within this many pixels of a label already in\n"
            "the project. 0 switches it off.")
        self.skip_reviewed = QCheckBox("and what was reviewed before")
        self.skip_reviewed.setToolTip(
            "No candidate where the last run's file already holds an\n"
            "answer, within its Same chip within distance or half the\n"
            "candidate's size, whichever is more.")
        skip.addWidget(self.skip_labels)
        skip.addWidget(self.skip_reviewed)
        skip.addStretch(1)
        grid.addRow("Skip within", skip)
        layout.addWidget(common)

        self.doc = QPlainTextEdit()
        self.doc.setReadOnly(True)
        self.doc.setMinimumHeight(140)
        layout.addWidget(QLabel("How this works"))
        layout.addWidget(self.doc, 1)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Sensitivity", "Labels found", "Candidates per image"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setToolTip(
            "What each sensitivity would have found of the labels on the\n"
            "images looked at. Double-click a row to take its sensitivity.")
        self.table.cellDoubleClicked.connect(self._take_row)
        self.table.setVisible(False)
        layout.addWidget(self.table)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check against my labels")
        self.check_button.setToolTip(
            "Run the detector over the labelled images among those chosen\n"
            "and say, for a range of sensitivities, what share of the labels\n"
            "a candidate would have landed on and how many candidates an\n"
            "image would get.")
        self.check_button.clicked.connect(self.check)
        buttons.addWidget(self.check_button)
        self.run_button = QPushButton("Run")
        self.run_button.setToolTip(
            "Find the candidates, write them to a file beside the project\n"
            "and open it in Model Review.")
        self.run_button.clicked.connect(self.run)
        buttons.addWidget(self.run_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.resize(640, 760)

    def _show_detector(self):
        """The chosen detector's controls, each with its line, and the
        account below."""
        detector = by_key(self.detector_combo.currentData())
        current = {key: self._param_value(key)
                   for key in self._param_widgets}
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self._param_widgets = {}
        for p in detector.PARAMETERS:
            if p.choices:
                widget = QComboBox()
                for choice in p.choices:
                    widget.addItem(choice, choice)
                value = current.get(p.key, p.default)
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else 0)
            else:
                widget = QDoubleSpinBox()
                widget.setRange(p.low, p.high)
                widget.setSingleStep(p.step)
                widget.setDecimals(0 if p.step >= 1 else 2)
                if p.unit:
                    widget.setSuffix(" " + p.unit)
                widget.setKeyboardTracking(False)
                widget.setValue(float(current.get(p.key, p.default)))
            widget.setToolTip(p.doc)
            self.params_form.addRow(p.label, widget)
            self._param_widgets[p.key] = widget
        self.doc.setPlainText(detector.describe() + "\n\n" + COMMON_DOC)

    def _param_value(self, key):
        widget = self._param_widgets[key]
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return widget.value()

    # -- the settings ---------------------------------------------------------

    def settings(self) -> Settings:
        out = Settings(
            detector=self.detector_combo.currentData(),
            params={key: self._param_value(key)
                    for key in self._param_widgets},
            sensitivity=self.sensitivity.value(),
            smallest=self.smallest.value(), largest=self.largest.value(),
            unit=self.unit.currentData(), per_image=self.per_image.value(),
            skip_labels_px=self.skip_labels.value(),
            skip_reviewed=self.skip_reviewed.isChecked(),
            tolerance_px=review_tolerance())
        if out.largest < out.smallest:
            out.largest = out.smallest
        return out

    def set_settings(self, settings: Settings):
        index = self.detector_combo.findData(settings.detector)
        self.detector_combo.setCurrentIndex(index if index >= 0 else 0)
        self._show_detector()
        for key, widget in self._param_widgets.items():
            if key not in settings.params:
                continue
            if isinstance(widget, QComboBox):
                i = widget.findData(settings.params[key])
                if i >= 0:
                    widget.setCurrentIndex(i)
            else:
                try:
                    widget.setValue(float(settings.params[key]))
                except (TypeError, ValueError):
                    pass
        self.sensitivity.setValue(settings.sensitivity)
        self.smallest.setValue(settings.smallest)
        self.largest.setValue(settings.largest)
        index = self.unit.findData(settings.unit)
        self.unit.setCurrentIndex(index if index >= 0 else 0)
        self.per_image.setValue(settings.per_image)
        self.skip_labels.setValue(settings.skip_labels_px)
        self.skip_reviewed.setChecked(settings.skip_reviewed)

    def images_choice(self) -> str:
        return self.images_combo.currentData()

    def _restore(self):
        stored = app_settings()
        text = stored.value(_SETTINGS_KEY, "")
        settings = Settings()
        if text:
            try:
                settings = Settings.from_dict(json.loads(str(text)))
            except (TypeError, ValueError):
                pass
        self.set_settings(settings)
        index = self.images_combo.findData(str(stored.value(_IMAGES_KEY,
                                                            SELECTED)))
        self.images_combo.setCurrentIndex(index if index >= 0 else 0)

    def save_settings(self):
        stored = app_settings()
        stored.setValue(_SETTINGS_KEY, json.dumps(self.settings().to_dict()))
        stored.setValue(_IMAGES_KEY, self.images_combo.currentData())

    # -- the work -------------------------------------------------------------

    def _images(self) -> list:
        return list(self._images_for(self.images_combo.currentData()) or [])

    def _busy(self, on: bool):
        for widget in (self.check_button, self.run_button, self.images_combo,
                       self.detector_combo, self.params_box):
            widget.setEnabled(not on)
        self.stop_button.setEnabled(on)
        if on:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def _start(self, job, done):
        if self._worker is not None:
            return
        self._busy(True)
        worker = Worker(job, self)
        worker.progress.connect(self._on_progress)
        worker.done.connect(done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, text, done, total):
        self.status.setText(text)
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))

    def _on_failed(self, text):
        self.status.setText("Failed: " + text)

    def _on_finished(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self._busy(False)

    def stop(self):
        if self._worker is not None:
            self._worker.stop()
            self.status.setText("Stopping...")

    def check(self):
        """What each sensitivity would have found of the labels."""
        images = [image for image in self._images()
                  if getattr(image, "labels", None)]
        if not images:
            self.status.setText("No labelled image among those chosen: "
                                "nothing to check against.")
            return
        self.save_settings()
        settings = self.settings()
        project = self._project()
        self._start(lambda progress, should_stop: check(
            project, images, settings, DEFAULT_SWEEP, progress=progress,
            should_stop=should_stop), self._on_checked)

    def _on_checked(self, result):
        if self._closing:
            return
        self.table.setRowCount(len(result.points))
        for row, point in enumerate(result.points):
            recall = f"{point.recall:.0%}" if point.labels else "-"
            cells = (f"{point.sensitivity:g}",
                     f"{recall} of {point.labels}",
                     f"{point.per_image:.1f}" if point.images else "-")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self.table.setVisible(True)
        self.table.resizeColumnsToContents()
        # Every row in view: a sweep is a handful of rows, and a scrolled
        # table hides the figure the user is looking for.
        rows = self.table.rowCount()
        if rows:
            self.table.setMinimumHeight(
                self.table.horizontalHeader().height()
                + self.table.rowHeight(0) * rows + 4)
        extra = []
        if result.unreadable:
            extra.append(f"{result.unreadable} could not be read")
        if result.stopped:
            extra.append("stopped early")
        self.status.setText(
            f"Checked against the labels on {result.images} image"
            f"{'' if result.images == 1 else 's'}. Double-click a row to "
            "take its sensitivity."
            + (" " + "; ".join(extra) + "." if extra else ""))

    def _take_row(self, row, _column):
        item = self.table.item(row, 0)
        if item is not None:
            try:
                self.sensitivity.setValue(float(item.text()))
            except ValueError:
                pass

    def run(self):
        """Find the candidates, write the file, hand it over."""
        images = self._images()
        if not images:
            self.status.setText("No image to look at among those chosen.")
            return
        target = self._target_path()
        if target is None:
            return
        self.save_settings()
        settings = self.settings()
        project = self._project()
        self._target, self._run_settings = target, settings
        # The last run's file is the memory (2.9.4): what it says was
        # ignored or imported is skipped, and carried onto what is found
        # again.
        self._previous = read_outcomes(target)
        memory = LedgerIndex(self._previous, settings.tolerance_px)

        def job(progress, should_stop):
            # The file is written here, on the window's thread, once the
            # result is wanted: a run cut short by a close wrote a partial
            # file over the last one and opened it (2.9.1).
            return run(project, images, settings, progress, should_stop,
                       memory=memory)
        self._start(job, self._on_ran)

    def _target_path(self) -> "Path | None":
        """Beside the project; a method of its own so the main window can
        ask where when the project is unsaved."""
        project_path = self._path()
        if project_path is None:
            self.status.setText("Save the project first: the candidates "
                                "file is written beside it.")
            return None
        return candidates_path(project_path)

    def _on_ran(self, result):
        if self._closing or self._target is None:
            return
        records, report = result
        try:
            write_candidates(self._target, records, self._run_settings,
                             report, previous=self._previous)
        except OSError as exc:
            self.status.setText(f"Could not write {self._target}: {exc}")
            return
        self.status.setText(f"Written: {report.summary()}.")
        self.finished_run.emit(str(self._target), report)

    def showEvent(self, event):
        self._closing = False
        super().showEvent(event)

    def closeEvent(self, event):
        self.save_settings()
        self._closing = True            # whatever ends now is not wanted
        if self._worker is not None:
            self._worker.stop()
            if not self._worker.wait(CLOSE_WAIT_MS):
                # Still reading: a QThread destroyed while running
                # aborts the whole process. It goes on alone, its
                # result unheard, and deletes itself when done (2.9.1).
                worker = self._worker
                for signal, slot in ((worker.progress, self._on_progress),
                                     (worker.failed, self._on_failed),
                                     (worker.finished, self._on_finished)):
                    signal.disconnect(slot)
                try:
                    worker.done.disconnect()
                except TypeError:
                    pass
                worker.setParent(None)
                worker.finished.connect(worker.deleteLater)
                self._worker = None
                self._busy(False)
                self.status.setText("Stopped; the run is finishing on "
                                    "its own.")
        super().closeEvent(event)
