"""Chips against the project's labels (2.9.4): a chip whose centre lies
within a distance of a label already on its image is "labelled
already", and the window says so.

The labels are the truth of what is labelled, so the comparison is
made again whenever the labels change - a label placed by hand on the
map, removed, moved, an import undone - and never goes stale as a
stored memory did. A chip's own label - the one an import made or took
over for it, named in the file's note - does not count: that chip is
imported, not labelled by something else.

The labels are bucketed by image and position so a round of thousands
of chips against a project of twenty thousand labels is a few lookups
per chip, not a comparison of each with every label of its image.

Qt-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .. import duplicates


@dataclass(frozen=True)
class Near:
    """A label within the distance of a chip."""
    label_id: int
    class_name: str
    pixel_x: float
    pixel_y: float
    distance: float

    def describe(self) -> str:
        """"vessel, 3 px off"."""
        return f"{self.class_name}, {self.distance:.0f} px off"


def default_distance() -> float:
    """The Import Options distance: one number means "the same object"
    everywhere - duplicates on import, the detector's skip, and this."""
    return duplicates.distance_px()


class LabelIndex:
    """The project's labels, by image path and position."""

    def __init__(self, project, radius: float):
        self.radius = max(0.0, float(radius))
        self._cell = max(self.radius, 1e-6)
        self._buckets: dict = {}
        images = getattr(project, "images", {}) or {}
        for path, image in images.items():
            for label in getattr(image, "labels", []) or []:
                key = (path, int(label.pixel_x // self._cell),
                       int(label.pixel_y // self._cell))
                self._buckets.setdefault(key, []).append(label)

    def near(self, image_path: str, pixel_x: float, pixel_y: float,
             exclude: "int | None" = None) -> list:
        """The labels within the radius of this place on this image,
        nearest first, ``exclude`` (a label id) left out."""
        column = int(pixel_x // self._cell)
        row = int(pixel_y // self._cell)
        found = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for label in self._buckets.get(
                        (image_path, column + dx, row + dy), ()):
                    if label.id == exclude:
                        continue
                    distance = math.hypot(label.pixel_x - pixel_x,
                                          label.pixel_y - pixel_y)
                    if distance <= self.radius:
                        found.append(Near(label.id, label.class_name,
                                          float(label.pixel_x),
                                          float(label.pixel_y), distance))
        found.sort(key=lambda n: (n.distance, n.label_id))
        return found


def near_labels(project, image_path: str, pixel_x: float, pixel_y: float,
                radius: float, exclude: "int | None" = None) -> list:
    """The labels within ``radius`` of one place, nearest first: one
    image's labels looked through, for a single chip (a re-centre)."""
    images = getattr(project, "images", {}) or {}
    image = images.get(image_path)
    found = []
    for label in getattr(image, "labels", []) or []:
        if label.id == exclude:
            continue
        distance = math.hypot(label.pixel_x - pixel_x, label.pixel_y - pixel_y)
        if distance <= radius:
            found.append(Near(label.id, label.class_name, float(label.pixel_x),
                              float(label.pixel_y), distance))
    found.sort(key=lambda n: (n.distance, n.label_id))
    return found


def mark_near(chips, project, radius: float) -> bool:
    """Give every chip its ``near`` list; True when any chip's standing
    changed (labelled already, or no longer)."""
    index = LabelIndex(project, radius)
    changed = False
    for chip in chips:
        own = chip.earlier.label_id if chip.earlier is not None else None
        before = chip.labelled_already()
        chip.near = index.near(chip.image_path, chip.pixel_x, chip.pixel_y,
                               exclude=own)
        if chip.labelled_already() != before:
            changed = True
    return changed
