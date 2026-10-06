"""The review memory: what the model called each chip, and what it became.

Kept in the project file (format 5.0, top-level "model_review"), one
record per verdict given in the Model Review window and imported. It is
what makes the loop iterative: when the next round's file comes in, a
chip already reviewed - same image, centre within a few pixels - is kept
off the grid, and anyone can read what the model said of a label and
what a person made of it.

A chip is named by its image's FILE NAME, not its path: that is how the
team's file names it, and it is what survives a project whose imagery
was moved (Locate Missing Images) or passed to another machine.

Qt-free; the window and the importer use it, tests exercise it alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PurePosixPath

# The verdict that is not a class: looked at, and nothing to label.
IGNORE = "ignore"
# Two centres this close on the same image are the same chip.
DEFAULT_TOLERANCE_PX = 8.0


def image_key(name_or_path) -> str:
    """A file name as it is compared: the last path part, lower-cased,
    whichever OS wrote the path."""
    text = str(name_or_path or "").replace("\\", "/")
    return PurePosixPath(text).name.lower()


@dataclass
class Verdict:
    """One reviewed chip."""
    image: str              # the image's file name, no directory
    pixel_x: float          # centre, at the project image's resolution
    pixel_y: float
    predicted: str          # what the model called it
    score: float            # how sure it was
    became: str             # a class name, or IGNORE
    source: str = ""        # the file it came from (its name)
    label_id: int | None = None   # the label it made, when it made one
    by: str = ""
    at: str = ""

    def ignored(self) -> bool:
        return self.became == IGNORE

    def to_dict(self) -> dict:
        data = {"image": self.image, "x": float(self.pixel_x),
                "y": float(self.pixel_y), "predicted": self.predicted,
                "score": float(self.score), "became": self.became}
        if self.source:
            data["source"] = self.source
        if self.label_id is not None:
            data["label_id"] = int(self.label_id)
        if self.by:
            data["by"] = self.by
            if self.at:
                data["at"] = self.at
        return data

    @classmethod
    def from_dict(cls, data) -> "Verdict | None":
        """A verdict read from a file, or None for one that cannot be."""
        if not isinstance(data, dict):
            return None
        try:
            image = str(data["image"])
            x, y = float(data["x"]), float(data["y"])
            predicted = str(data["predicted"])
            became = str(data["became"])
        except (KeyError, TypeError, ValueError):
            return None
        try:
            score = float(data.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0             # a score is a detail; the verdict is not
        if not image or not became or not (math.isfinite(x)
                                           and math.isfinite(y)):
            return None
        label_id = data.get("label_id")
        try:
            label_id = None if label_id is None else int(label_id)
        except (TypeError, ValueError):
            label_id = None
        return cls(image=image, pixel_x=x, pixel_y=y, predicted=predicted,
                   score=score if math.isfinite(score) else 0.0,
                   became=became, source=str(data.get("source") or ""),
                   label_id=label_id, by=str(data.get("by") or ""),
                   at=str(data.get("at") or ""))


def verdicts_from_project_data(data) -> list:
    """The "model_review" entry of a project file, parsed; unreadable
    records are left out."""
    if not isinstance(data, list):
        return []
    verdicts = []
    for item in data:
        verdict = Verdict.from_dict(item)
        if verdict is not None:
            verdicts.append(verdict)
    return verdicts


def find_verdict(verdicts, image, pixel_x: float, pixel_y: float,
                 tolerance: float = DEFAULT_TOLERANCE_PX) -> "Verdict | None":
    """The earlier verdict on this chip, if there is one: the same image
    by file name, and the nearest centre within ``tolerance`` pixels."""
    key = image_key(image)
    best, best_distance = None, None
    for verdict in verdicts:
        if image_key(verdict.image) != key:
            continue
        distance = math.hypot(verdict.pixel_x - pixel_x,
                              verdict.pixel_y - pixel_y)
        if distance <= tolerance and (best is None
                                      or distance < best_distance):
            best, best_distance = verdict, distance
    return best
