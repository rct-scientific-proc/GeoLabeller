"""The chips to evaluate on: a review file matched onto a project with
the application's own matcher, each chip given the verdict the project
remembers for it. The verdicts are the truth the techniques are scored
against; a chip without one is carried but not scored."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.labels import LabelProject
from app.model_review.ledger import IGNORE, LedgerIndex
from app.model_review.source import match_candidates, read_candidates


@dataclass
class Record:
    image_path: str
    pixel_x: float
    pixel_y: float
    predicted: str          # what the model called it
    score: float
    truth: str | None       # the verdict: a class, IGNORE, or None

    @property
    def place(self) -> tuple:
        return (self.image_path, self.pixel_x, self.pixel_y)


@dataclass
class ReviewSet:
    name: str
    records: list
    predicted_classes: set  # every class the file's model predicts

    @property
    def reviewed(self) -> list:
        return [r for r in self.records if r.truth is not None]

    def is_new(self, truth: str) -> bool:
        """A verdict naming a class the model never predicts: one the
        reviewer had to create."""
        return truth != IGNORE and truth not in self.predicted_classes

    def summary(self) -> str:
        reviewed = self.reviewed
        truths = sorted({r.truth for r in reviewed})
        new = [t for t in truths if self.is_new(t)]
        return (f"{len(self.records)} chips, {len(reviewed)} with a verdict; "
                f"predicted classes {sorted(self.predicted_classes)}; "
                f"verdict classes {truths}; new to the model: {new or 'none'}")


def load_review(project_path, review_path) -> tuple:
    """(ReviewSet, MatchReport) for a project file and a review file."""
    project = LabelProject.load(project_path)
    candidates, malformed = read_candidates(review_path)
    chips, report = match_candidates(candidates, project, malformed)
    index = LedgerIndex(getattr(project, "model_review", []) or [])
    records = []
    for chip in chips:
        verdict = index.find(chip.image_name, chip.source_x, chip.source_y)
        records.append(Record(chip.image_path, chip.pixel_x, chip.pixel_y,
                              chip.predicted, chip.score,
                              verdict.became if verdict else None))
    return (ReviewSet(Path(review_path).stem, records,
                      {chip.predicted for chip in chips}), report)
