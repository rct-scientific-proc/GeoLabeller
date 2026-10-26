"""A sensitivity checked against the project's own labels.

Before anyone reviews a candidate: run the detector over the images
that have labels and say, for each sensitivity in a sweep, what share of
the labels a candidate would have landed on (recall) and how many
candidates an image would get. One pass per image serves the whole
sweep: the detector is run at the lowest sensitivity and the sweep
re-thresholds what it found. The figures are what the user picks a
sensitivity by.

Qt-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import by_key
from .run import Settings, detect_image, sizes_in_pixels

DEFAULT_SWEEP = (2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0)


@dataclass
class Point:
    """One sensitivity's figures over every labelled image checked."""
    sensitivity: float
    found: int = 0              # labels with a candidate within reach
    labels: int = 0
    candidates: int = 0         # over every image
    images: int = 0

    @property
    def recall(self) -> float:
        return self.found / self.labels if self.labels else math.nan

    @property
    def per_image(self) -> float:
        return self.candidates / self.images if self.images else math.nan

    def describe(self) -> str:
        recall = f"{self.recall:.0%}" if self.labels else "-"
        per_image = f"{self.per_image:.1f}" if self.images else "-"
        return (f"{self.sensitivity:g} sigma: {recall} of {self.labels} "
                f"labels found, {per_image} candidates per image")


@dataclass
class Check:
    points: list = field(default_factory=list)
    images: int = 0
    unreadable: int = 0
    stopped: bool = False

    def describe(self) -> str:
        lines = [p.describe() for p in self.points]
        if self.unreadable:
            lines.append(f"{self.unreadable} image"
                         f"{'' if self.unreadable == 1 else 's'} could not "
                         "be read")
        if self.stopped:
            lines.append("stopped early")
        return "\n".join(lines)


def check(project, images, settings: Settings, sweep=DEFAULT_SWEEP,
          reach: "float | None" = None, progress=None,
          should_stop=None) -> Check:
    """The sweep over the labelled images among ``images``. A label is
    found when it lies within a candidate's own patch (or the smallest
    size, if that is more) - or within ``reach`` of one, in the
    settings' unit, when given. Labels already there are not skipped
    here, whatever the settings say: they are the test."""
    progress = progress or (lambda text, done, total: None)
    should_stop = should_stop or (lambda: False)
    sweep = sorted(float(s) for s in sweep)
    floor = sweep[0]
    detector = by_key(settings.detector)(**settings.params)
    labelled = [image for image in images
                if getattr(image, "labels", None)]
    plain = Settings.from_dict(settings.to_dict())
    plain.skip_labels_px = 0.0
    plain.skip_reviewed = False
    plain.per_image = 0
    out = Check(points=[Point(s) for s in sweep])
    total = len(labelled)
    for done, image in enumerate(labelled):
        if should_stop():
            out.stopped = True
            break
        progress(f"Checking {image.name}", done, total)
        records, stats = detect_image(image, project, plain, detector,
                                      floor=floor)
        if stats.error:
            out.unreadable += 1
            continue
        out.images += 1
        smallest_px, largest_px, _ground = sizes_in_pixels(
            image, settings, records[0]["image_width"] if records else None)
        # A label is found by a candidate it lies within: the candidate's
        # own patch, or the smallest size, whichever is larger - not
        # half the largest size, which found labels 25 m off (2.9.1).
        # ``reach``, when given, is a fixed radius in the settings' unit.
        if reach is None:
            radius = None
            floor_px = max(4.0, smallest_px)
        else:
            radius = reach * (largest_px / settings.largest
                              if settings.largest else 1.0)
        labels = [(float(lab.pixel_x), float(lab.pixel_y))
                  for lab in image.labels]
        strengths = np.array([r["strength"] for r in records], dtype=float)
        xs = np.array([r["x"] for r in records], dtype=float)
        ys = np.array([r["y"] for r in records], dtype=float)
        sizes = np.array([r["size_px"] for r in records], dtype=float)
        for point in out.points:
            take = strengths >= point.sensitivity
            if settings.per_image > 0 and take.sum() > settings.per_image:
                order = np.argsort(-strengths)
                chosen = np.zeros_like(take)
                chosen[order[:settings.per_image]] = True
                take &= chosen
            cx, cy = xs[take], ys[take]
            within = (np.full(cx.shape, radius) if radius is not None
                      else np.maximum(floor_px, sizes[take] / 2.0))
            point.images += 1
            point.candidates += int(take.sum())
            point.labels += len(labels)
            for lx, ly in labels:
                if cx.size and bool((np.hypot(cx - lx, cy - ly) <= within).any()):
                    point.found += 1
    progress("Done", total, total)
    return out
