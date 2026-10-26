"""The review memory: what the model called each chip, and what it became.

Kept in the project file (format 5.0, top-level "model_review"), one
record per verdict given in the Model Review window and imported. It is
what makes the loop iterative: when the next round's file comes in, a
chip already reviewed - same image, centre within a few pixels - is kept
off the grid, and anyone can read what the model said of a label and
what a person made of it.

A chip is named by its image's FILE NAME, not its path: that is how the
team's file names it, and it is what survives a project whose imagery
was moved (Locate Missing Images) or passed to another machine. Since
2.8.17 an entry also carries the image's group in the project, so two
same-named images in two groups keep their own entries: a preference
when several entries lie within reach, never a bar to matching.

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
    group: str = ""         # the image's group in the project (2.8.17)

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
        if self.group:
            data["group"] = self.group
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
                   at=str(data.get("at") or ""),
                   group=str(data.get("group") or ""))


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


class LedgerIndex:
    """The verdicts, bucketed by image and position, so the one on a
    chip is found in constant time whatever the size of the memory.

    Buckets are ``tolerance`` pixels square: every centre within
    ``tolerance`` of a chip lies in its bucket or one of the eight
    around it. Comparing each chip with every verdict instead took half
    a minute at 5,000 of each (measured 2026-10-06).
    """

    def __init__(self, verdicts, tolerance: float = DEFAULT_TOLERANCE_PX):
        self.tolerance = float(tolerance)
        self._cell = max(self.tolerance, 1e-6)
        self._buckets: dict = {}
        for verdict in verdicts:
            key = (image_key(verdict.image),
                   int(verdict.pixel_x // self._cell),
                   int(verdict.pixel_y // self._cell))
            self._buckets.setdefault(key, []).append(verdict)

    def __len__(self):
        return sum(len(bucket) for bucket in self._buckets.values())

    def find(self, image, pixel_x: float, pixel_y: float,
             predicted: "str | None" = None,
             group: "str | None" = None) -> "Verdict | None":
        """The earlier verdict on this chip, if there is one: the same
        image by file name, and the nearest centre within the tolerance.
        Given ``group`` or ``predicted``, an entry of the same group,
        then one the model called that class, comes first whatever the
        distance: one object flagged under two classes is two chips at
        one centre with an entry each (2.8.16), and a same-named image
        in another group has entries of its own (2.8.17); by distance
        alone they all found the same one."""
        return self.rank(image, pixel_x, pixel_y, predicted, group)[1]

    def rank(self, image, pixel_x: float, pixel_y: float,
             predicted: "str | None" = None,
             group: "str | None" = None) -> tuple:
        """(how good, the entry) for the best entry on this chip - the
        key sorts a worse answer after a better - or (None, None)."""
        found = self.candidates(image, pixel_x, pixel_y, predicted, group)
        return found[0] if found else (None, None)

    def candidates(self, image, pixel_x: float, pixel_y: float,
                   predicted: "str | None" = None,
                   group: "str | None" = None) -> list:
        """Every entry within the tolerance of this chip as (how good,
        the entry), the best first: the chip's own group, then an entry
        of no group (from before 2.8.17: any group's), then another
        group's; within those the same predicted class, then the
        nearest."""
        name = image_key(image)
        column, row = int(pixel_x // self._cell), int(pixel_y // self._cell)
        found = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for verdict in self._buckets.get(
                        (name, column + dx, row + dy), ()):
                    distance = math.hypot(verdict.pixel_x - pixel_x,
                                          verdict.pixel_y - pixel_y)
                    if distance > self.tolerance:
                        continue
                    if group is not None and verdict.group == group:
                        own = 0             # the chip's own group
                    elif not verdict.group or group is None:
                        own = 1             # no group known: any group's
                    else:
                        own = 2             # another group's
                    key = (own, predicted is not None
                           and verdict.predicted != predicted, distance)
                    found.append((key, verdict))
        found.sort(key=lambda item: item[0])
        return found


    def within(self, image, pixel_x: float, pixel_y: float, radius: float,
               group: "str | None" = None) -> list:
        """Every entry within ``radius`` of this place on this image -
        any radius, not only the tolerance - leaving out entries of
        another group when ``group`` is given (a same-named image
        elsewhere is not this one). For Find Candidates' skip (2.9.1)."""
        name = image_key(image)
        radius = max(0.0, float(radius))
        column, row = int(pixel_x // self._cell), int(pixel_y // self._cell)
        reach = int(radius // self._cell) + 1
        found = []
        for dx in range(-reach, reach + 1):
            for dy in range(-reach, reach + 1):
                for verdict in self._buckets.get(
                        (name, column + dx, row + dy), ()):
                    if group is not None and verdict.group \
                            and verdict.group != group:
                        continue
                    if math.hypot(verdict.pixel_x - pixel_x,
                                  verdict.pixel_y - pixel_y) <= radius:
                        found.append(verdict)
        return found


def find_verdict(verdicts, image, pixel_x: float, pixel_y: float,
                 tolerance: float = DEFAULT_TOLERANCE_PX,
                 predicted: "str | None" = None,
                 group: "str | None" = None) -> "Verdict | None":
    """One lookup; for many, build a LedgerIndex once."""
    return LedgerIndex(verdicts, tolerance).find(image, pixel_x, pixel_y,
                                                predicted, group)
