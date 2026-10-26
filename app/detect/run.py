"""A detector over the project's images: the reads, the sizes, the
skips, the file.

Each image is read decimated to a working scale at which the smallest
object is a handful of pixels across - the detectors need no more, and
a 20,000-pixel image would take minutes at full size - as the mean of
the source bands the display draws (their raw values, not the stretched
picture: the statistics must not move with the display settings), with
nodata left out. Sizes are metres where the image has ground, by its
geodesic width, and pixels where it has not; an image without ground
takes the metre figures as pixels and is counted. The candidates are
written at full resolution into a file beside the project in the Model
Review file format, their class "candidate", so Model Review opens it
like any other.

Qt-free; detect_dialog.py puts the window round ``run``.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import gdal_config, display_settings
from ..labels import geodesic_distance
from ..model_review.ledger import DEFAULT_TOLERANCE_PX, LedgerIndex
from ..model_review.source import CANDIDATE
from ..snippets import nodata_mask
from ..version import app_version
from . import by_key
from .base import score_of

CLASS = CANDIDATE               # what the review grid calls them
METRES, PIXELS = "m", "px"
SMALLEST_WORKING_PX = 6.0       # the smallest object, at the working scale
MAX_WORKING_SIDE = 3000         # and the image no larger than this a side
FILE_SUFFIX = ".candidates.json"


@dataclass
class Settings:
    """What a run is told; remembered by the dialog."""
    detector: str = "contrast"
    params: dict = field(default_factory=dict)
    sensitivity: float = 4.0    # robust sigmas a peak must reach
    smallest: float = 2.0       # object size across...
    largest: float = 50.0       # ...in ``unit``
    unit: str = METRES
    per_image: int = 10         # candidates per image at most
    skip_labels_px: float = 10.0    # none within this of a label (full px)
    skip_reviewed: bool = True      # none within the tolerance of the memory
    tolerance_px: float = DEFAULT_TOLERANCE_PX

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "Settings":
        out = cls()
        if not isinstance(data, dict):
            return out
        for key, value in data.items():
            if key == "params" and isinstance(value, dict):
                out.params = dict(value)
            elif hasattr(out, key) and key != "params":
                try:
                    out.__dict__[key] = type(getattr(out, key))(value)
                except (TypeError, ValueError):
                    pass
        return out


@dataclass
class ImageStats:
    path: str
    found: int = 0              # candidates written
    seen: int = 0               # peaks at the working scale, any size
    near_label: int = 0         # dropped: a label is there already
    reviewed: int = 0           # dropped: in the memory
    outside_size: int = 0       # dropped: too small or too large
    capped: int = 0             # dropped: over the per-image limit
    factor: float = 1.0         # working-scale pixels per source pixel
    ground: bool = True         # sizes taken in metres
    error: str = ""


@dataclass
class Report:
    images: int = 0
    unreadable: int = 0
    no_ground: int = 0          # sizes taken as pixels
    found: int = 0
    near_label: int = 0
    reviewed: int = 0
    stopped: bool = False
    stats: list = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.found} candidate{'' if self.found == 1 else 's'} "
                 f"on {self.images} image{'' if self.images == 1 else 's'}"]
        if self.near_label:
            parts.append(f"{self.near_label} on labels already there left "
                         "out")
        if self.reviewed:
            parts.append(f"{self.reviewed} reviewed before left out")
        if self.no_ground:
            parts.append(f"{self.no_ground} image"
                         f"{'' if self.no_ground == 1 else 's'} without "
                         "georeferencing, sizes taken in pixels")
        if self.unreadable:
            parts.append(f"{self.unreadable} could not be read")
        if self.stopped:
            parts.append("stopped early")
        return "; ".join(parts)


# -- sizes -------------------------------------------------------------------

def metres_per_pixel(image, width: "int | None" = None) -> "float | None":
    """Metres one source pixel spans, by the image's geodesic width, or
    None without ground. ``width`` is the raster's when the record
    has none (2.9.1: without it, a georeferenced image whose size was
    never recorded took its metres as pixels)."""
    if not width:
        width = int(getattr(image, "original_width", 0) or 0)
    if width <= 0 or image.get_affine() is None or image.get_crs() is None:
        return None
    try:
        left = image.pixel_to_latlon(0.0, 0.0)
        right = image.pixel_to_latlon(float(width), 0.0)
    except Exception:  # noqa: BLE001 - a broken CRS: no ground
        return None
    if left is None or right is None:
        return None
    metres = geodesic_distance(left[0], left[1], right[0], right[1])
    if not math.isfinite(metres) or metres <= 0:
        return None
    return metres / width


def sizes_in_pixels(image, settings: Settings,
                    width: "int | None" = None) -> tuple:
    """(smallest px, largest px, ground): the size range in source pixels,
    and whether metres could be used. ``width`` as metres_per_pixel."""
    if settings.unit == PIXELS:
        return float(settings.smallest), float(settings.largest), True
    mpp = metres_per_pixel(image, width)
    if mpp is None:
        return float(settings.smallest), float(settings.largest), False
    return settings.smallest / mpp, settings.largest / mpp, True


def working_factor(smallest_px: float, width: int, height: int) -> float:
    """Source pixels per working pixel: the smallest object at least
    SMALLEST_WORKING_PX across, the image at most MAX_WORKING_SIDE a
    side, never finer than the source."""
    factor = max(1.0, smallest_px / SMALLEST_WORKING_PX)
    side = max(width, height)
    if side / factor > MAX_WORKING_SIDE:
        factor = side / MAX_WORKING_SIDE
    return factor


# -- reading -----------------------------------------------------------------

def read_size(path: str) -> tuple:
    """(width, height) from the raster's header."""
    with gdal_config.opened(path) as src:
        return int(src.width), int(src.height)


def read_values(path: str, factor: float) -> tuple:
    """(values, mask, width, height): the mean of the bands the display
    draws, decimated by ``factor``, float32 with NaN where there is no
    data; ``mask`` True where there is."""
    display = display_settings.for_image(path)
    with gdal_config.opened(path) as src:
        width, height = int(src.width), int(src.height)
        out_h = max(1, int(round(height / factor)))
        out_w = max(1, int(round(width / factor)))
        bands = sorted(set(display.source_bands(src.count)))
        data = src.read(indexes=bands, out_shape=(len(bands), out_h, out_w))
        empty = None
        for plane in data:
            missing = nodata_mask(plane, src.nodata)
            if missing is not None:
                empty = missing if empty is None else (empty | missing)
        values = data.astype(np.float32).mean(axis=0)
        if empty is not None:
            values[empty] = np.nan
    mask = np.isfinite(values)
    return values, mask, width, height


# -- one image ---------------------------------------------------------------

def detect_image(image, project, settings: Settings, detector=None,
                 memory: "LedgerIndex | None" = None,
                 floor: "float | None" = None) -> tuple:
    """(records, stats) for one project image: the candidates as Model
    Review records at full resolution, sized, skipped and capped as the
    settings say. ``floor`` overrides the sensitivity (a sweep)."""
    path = image.path
    stats = ImageStats(path=path)
    detector = detector or by_key(settings.detector)(**settings.params)
    try:
        width0 = int(getattr(image, "original_width", 0) or 0)
        height0 = int(getattr(image, "original_height", 0) or 0)
        if not (width0 and height0):
            width0, height0 = read_size(path)   # not on record: the header
        smallest_px, largest_px, ground = sizes_in_pixels(image, settings,
                                                          width0)
        stats.ground = ground
        factor = working_factor(smallest_px, width0, height0)
        values, mask, width, height = read_values(path, factor)
    except Exception as exc:  # noqa: BLE001 - said in the report
        stats.error = f"{type(exc).__name__}: {exc}"
        return [], stats
    stats.factor = factor
    if not mask.any():
        return [], stats
    level = settings.sensitivity if floor is None else floor
    # At the working scale, and no object wider than a quarter of the
    # image: a background is judged over several objects, and a
    # largest size of half the image cost gigabytes (2.9.1).
    smallest_w = smallest_px / factor
    largest_w = max(smallest_w, min(largest_px / factor,
                                    max(values.shape) / 4.0))
    try:
        found = detector.detect(values, mask, smallest_w, largest_w, level)
    except Exception as exc:  # noqa: BLE001 - MemoryError among them
        stats.error = f"{type(exc).__name__}: {exc}"
        return [], stats
    stats.seen = len(found)
    # Back to source pixels, inside the image.
    scaled = []
    for c in found:
        x = min(max((c.x + 0.5) * factor, 0.0), width - 1e-6)
        y = min(max((c.y + 0.5) * factor, 0.0), height - 1e-6)
        half = c.size * factor / 2.0
        box = (tuple(v * factor for v in c.box) if c.box
               else (x - half, y - half, x + half, y + half))
        scaled.append((x, y, c.strength, c.size * factor, c.polarity,
                       box))
    # The size filter, with a third's grace either way: sizes are
    # estimates, and the user's figures round.
    kept = []
    for item in scaled:
        size = item[3]
        if size < smallest_px / 1.5 or size > largest_px * 1.5:
            stats.outside_size += 1
            continue
        kept.append(item)
    # One candidate per object: a long one gives a peak at each end, and
    # the weaker goes when the two overlap.
    kept = merge_overlapping(kept)
    # Not where a label is already, nor where the memory has an answer.
    if settings.skip_labels_px > 0:
        places = [(float(lab.pixel_x), float(lab.pixel_y))
                  for lab in getattr(image, "labels", []) or []]
        if places:
            lx = np.array([p[0] for p in places])
            ly = np.array([p[1] for p in places])
            kept, near = _drop_near(kept, lx, ly, settings.skip_labels_px)
            stats.near_label = near
    if settings.skip_reviewed and memory is not None:
        # Within Model Review's own distance, or half the candidate's
        # size if that is more (a re-run lands a big object's middle a
        # few pixels over); entries of another group's same-named image
        # are not this one's (2.9.1).
        name = Path(str(path).replace("\\", "/")).name
        group = getattr(image, "group", None) or ""
        still = []
        for item in kept:
            reach = max(settings.tolerance_px, item[3] / 2.0)
            if memory.within(name, item[0], item[1], reach, group):
                stats.reviewed += 1
            else:
                still.append(item)
        kept = still
    kept.sort(key=lambda item: -item[2])
    if settings.per_image > 0 and len(kept) > settings.per_image:
        stats.capped = len(kept) - settings.per_image
        kept = kept[:settings.per_image]
    records = [{"image": path, "image_width": width, "image_height": height,
                "x": round(x, 2), "y": round(y, 2), "class": CLASS,
                "score": round(score_of(strength), 4),
                "strength": round(strength, 2), "size_px": round(size, 1),
                "polarity": polarity}
               for x, y, strength, size, polarity, _box in kept]
    stats.found = len(records)
    return records, stats


def merge_overlapping(items: list) -> list:
    """(x, y, strength, size, polarity, box) tuples, the strongest
    kept of any two whose middles lie in each other's patch: a long
    object gives a peak at each end, and both patches are the object.
    The kept one stays where it is: given the middle of the two
    patches together, a small strong object in a long weak one's box
    was reported far from itself (2.9.1)."""
    kept: list = []
    for item in sorted(items, key=lambda it: -it[2]):
        x, y, _strength, _size, _polarity, box = item
        if any(_inside(x, y, kbox) or _inside(kx, ky, box)
               for kx, ky, _ks, _ksize, _kp, kbox in kept):
            continue
        kept.append(item)
    return kept


def _inside(x: float, y: float, box: tuple) -> bool:
    return box[0] - 1 <= x <= box[2] and box[1] - 1 <= y <= box[3]


def _drop_near(items: list, lx: np.ndarray, ly: np.ndarray,
               distance: float) -> tuple:
    kept, dropped = [], 0
    for item in items:
        d = np.hypot(lx - item[0], ly - item[1])
        if d.size and float(d.min()) <= distance:
            dropped += 1
        else:
            kept.append(item)
    return kept, dropped


# -- the run and the file -------------------------------------------------------

def run(project, images, settings: Settings, progress=None,
        should_stop=None) -> tuple:
    """(records, report) over ``images`` (project entries). ``progress``
    takes (text, done, total); ``should_stop()`` True ends the run with
    what there is."""
    progress = progress or (lambda text, done, total: None)
    should_stop = should_stop or (lambda: False)
    detector = by_key(settings.detector)(**settings.params)
    memory = None
    if settings.skip_reviewed:
        memory = LedgerIndex(getattr(project, "model_review", []) or [],
                             settings.tolerance_px)
    records: list = []
    report = Report()
    images = list(images)
    total = len(images)
    for done, image in enumerate(images):
        if should_stop():
            report.stopped = True
            break
        progress(f"Looking at {Path(str(image.path)).name}", done, total)
        found, stats = detect_image(image, project, settings, detector,
                                    memory)
        report.stats.append(stats)
        report.images += 1
        if stats.error:
            report.unreadable += 1
            continue
        if not stats.ground:
            report.no_ground += 1
        records.extend(found)
        report.found += stats.found
        report.near_label += stats.near_label
        report.reviewed += stats.reviewed
    progress("Done", total, total)
    return records, report


def candidates_path(project_path) -> Path:
    """The file beside the project: project.geolabel -> project.candidates.json."""
    path = Path(project_path)
    return path.with_name(path.stem + FILE_SUFFIX)


def write_candidates(path, records: list, settings: Settings,
                     report: "Report | None" = None) -> int:
    """The Model Review file: a {"chips": [...]} wrapper with how it was
    made, so a reader can tell one run from another."""
    detector = by_key(settings.detector)
    data = {
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "creator": f"GeoLabeller {app_version()}",
        "detector": detector.key,
        "settings": settings.to_dict(),
        "summary": report.summary() if report is not None else "",
        "chips": list(records),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
    return len(records)


def source_name(settings: Settings) -> str:
    """How the memory names a run: the detector and its sensitivity."""
    return f"{settings.detector} {settings.sensitivity:g} sigma"
