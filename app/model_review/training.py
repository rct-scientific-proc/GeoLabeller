"""Training off the GUI thread.

The window gathers what there is to learn from and the places still to
look at, hands them to a thread with a Learner (learner.py), and listens:
progress lines for the status, then the report and the suggestions - or
why it failed. Stop asks the learner to stop at its next check; the
thread ends with "Training stopped."

A QThread of its own rather than the global pool: an epoch is seconds of
work, and the image window's reads share that pool.
"""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from .learner import Cancelled, Learner


class TrainingThread(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(object, dict)         # Report, {chip key: Suggestion}
    failed = pyqtSignal(str)

    def __init__(self, learner: Learner, examples: list, places: list,
                 parent=None):
        """``places`` are (key, image_path, x, y) for the chips to suggest
        for, once trained."""
        super().__init__(parent)
        self._learner = learner
        self._examples = list(examples)
        self._places = list(places)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        should_stop = lambda: self._stop  # noqa: E731
        try:
            report = self._learner.train(self._examples, self.progress.emit,
                                         should_stop)
            self.progress.emit(f"Trained; suggesting for {len(self._places)} "
                               "chips...")
            suggestions = self._learner.suggest(
                [(path, x, y) for _key, path, x, y in self._places],
                self.progress.emit, should_stop)
            self.done.emit(report, {
                place[0]: suggestion
                for place, suggestion in zip(self._places, suggestions)
                if suggestion is not None})
        except Cancelled:
            self.failed.emit("Training stopped.")
        except ValueError as exc:               # too little to learn from
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - said in the window
            self.failed.emit(f"Training failed: {type(exc).__name__}: {exc}")
