"""Work off the GUI thread: training, suggesting, saving snippets.

The window builds a job - a callable taking (progress, should_stop) and
returning a result - and hands it to a LearnThread, which reports
progress as (text, done, total), then the result, or why it failed.
Stop asks the job to stop at its next check; the thread ends with
"Stopped."

A QThread of its own rather than the global pool: an epoch is seconds
of work, and the image window's reads share that pool.
"""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from .learner import Cancelled, Learner, WeightsMissing
from .snippet_store import SnippetStore


class LearnThread(QThread):
    progress = pyqtSignal(str, int, int)    # text, done, total (0 = unknown)
    done = pyqtSignal(object)               # the job's result
    failed = pyqtSignal(str)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self._job = job
        self._stop = False

    def stop(self):
        self._stop = True

    def stopping(self) -> bool:
        return self._stop

    def run(self):
        try:
            self.done.emit(self._job(self.progress.emit, lambda: self._stop))
        except Cancelled:
            self.failed.emit("Stopped.")
        except (ValueError, WeightsMissing) as exc:  # too little; no weights
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - said in the window
            self.failed.emit(f"Failed: {type(exc).__name__}: {exc}")


def train_job(learner: Learner, examples: list, places: list):
    """Train, then suggest for ``places`` - (key, path, x, y) - and
    return (report, {key: Suggestion})."""
    def job(progress, should_stop):
        report = learner.train(examples, progress, should_stop)
        return report, suggest_job(learner, places)(progress, should_stop)
    return job


def suggest_job(learner: Learner, places: list):
    """Suggest with the model as trained: {key: Suggestion}."""
    def job(progress, should_stop):
        suggestions = learner.suggest(
            [(path, x, y) for _key, path, x, y in places], progress,
            should_stop)
        return {place[0]: suggestion
                for place, suggestion in zip(places, suggestions)
                if suggestion is not None}
    return job


def save_job(store: SnippetStore, places: list, chip_size: int):
    """Read every (path, x, y) place into the store; returns how many
    were read."""
    def job(progress, should_stop):
        arrays = store.fetch(places, chip_size, progress, should_stop)
        return sum(1 for arr in arrays if arr is not None)
    return job
