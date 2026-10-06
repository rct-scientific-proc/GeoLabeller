"""The model team's file of mistakes, read and matched onto the project.

The file (asked for 2026-10-05, as simple as it could be made): a JSON
list, one record per chip -

    {"image": "survey_03.tif", "image_width": 8192, "image_height": 6144,
     "x": 1812.4, "y": 640.9, "class": "vessel", "score": 0.97}

``image`` is the file's name, in any directory or none: it is matched to
the project's images by name. ``x`` and ``y`` are the chip's centre in
decimal pixels of an image ``image_width`` by ``image_height``; the
project's image may be the same picture at another resolution, and the
centre is scaled across by the ratio of the sizes. (Fractions of the
image work by the same rule: width and height of 1, x and y in 0..1.)
Without a stated size the centre is taken at the image's own. ``class``
is what the model called it, ``score`` how sure it was, 0..1.

A few spellings are accepted for a field (``file`` for ``image``,
``confidence`` for ``score``, ...) and a {"chips": [...]} wrapper is
read like the bare list, so a file written casually still loads.

What is kept and what is not (the user's rules, 2026-10-05):

- a record whose class is not one of the project's classes is dropped;
- a record whose image is not in the project is rejected; the rest of
  the file still loads;
- a name the project holds twice (the same file in two groups) is
  matched by the longest shared folder tail when the record gives a
  directory, and rejected as ambiguous when it does not.

Qt-free.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .. import gdal_config
from ..debug_log import debug
from .ledger import Verdict, image_key

_IMAGE_KEYS = ("image", "file", "filename", "path", "image_path")
_X_KEYS = ("x", "pixel_x", "col", "column")
_Y_KEYS = ("y", "pixel_y", "row")
_WIDTH_KEYS = ("image_width", "width", "w")
_HEIGHT_KEYS = ("image_height", "height", "h")
_CLASS_KEYS = ("class", "predicted", "class_name", "label", "prediction")
_SCORE_KEYS = ("score", "confidence", "probability", "prob", "p")
_LIST_KEYS = ("chips", "items", "records", "mistakes", "predictions")


@dataclass(frozen=True)
class Candidate:
    """One chip as the file gives it."""
    index: int                  # its place in the file
    image: str                  # as written: a name, or a path
    x: float
    y: float
    image_width: int | None
    image_height: int | None
    predicted: str
    score: float

    @property
    def name(self) -> str:
        return Path(self.image.replace("\\", "/")).name


@dataclass
class Chip:
    """A candidate matched onto one of the project's images."""
    key: int                    # the candidate's index: the grid's id
    image_path: str             # the project's key for the image
    image_name: str             # its file name
    group: str
    pixel_x: float              # centre at the project image's resolution
    pixel_y: float
    predicted: str
    score: float
    rescaled: bool = False      # the file stated another size
    verdict: str | None = None  # a class, ledger.IGNORE, or not yet
    earlier: Verdict | None = None   # reviewed in an earlier round

    def reviewed_before(self) -> bool:
        return self.earlier is not None


@dataclass
class MatchReport:
    """What a file came to, once matched."""
    total: int = 0              # well-formed records
    malformed: int = 0          # records that could not be read
    matched: int = 0
    unknown_class: Counter = field(default_factory=Counter)  # class -> n
    no_image: Counter = field(default_factory=Counter)       # name -> n
    ambiguous: Counter = field(default_factory=Counter)      # name -> n
    unreadable: Counter = field(default_factory=Counter)     # name -> n

    @property
    def rejected(self) -> int:
        return (sum(self.no_image.values()) + sum(self.ambiguous.values())
                + sum(self.unreadable.values()))

    @property
    def dropped(self) -> int:
        return sum(self.unknown_class.values())

    def summary(self) -> str:
        parts = [f"{self.matched} chip{'' if self.matched == 1 else 's'} "
                 "matched"]
        if self.dropped:
            names = ", ".join(sorted(self.unknown_class))
            parts.append(f"{self.dropped} of a class the project does not "
                         f"have ({names})")
        if self.no_image:
            n = sum(self.no_image.values())
            parts.append(f"{n} on {len(self.no_image)} image"
                         f"{'' if len(self.no_image) == 1 else 's'} not in "
                         f"the project ({_some(self.no_image)})")
        if self.ambiguous:
            n = sum(self.ambiguous.values())
            parts.append(f"{n} on a name the project holds more than once "
                         f"({_some(self.ambiguous)})")
        if self.unreadable:
            n = sum(self.unreadable.values())
            parts.append(f"{n} on an image whose size could not be read "
                         f"({_some(self.unreadable)})")
        if self.malformed:
            parts.append(f"{self.malformed} record"
                         f"{'' if self.malformed == 1 else 's'} unreadable")
        return "; ".join(parts)


def _some(names, limit: int = 4) -> str:
    """A few of the names, and how many more."""
    shown = sorted(names)[:limit]
    text = ", ".join(shown)
    more = len(names) - len(shown)
    return f"{text}, and {more} more" if more > 0 else text


# -- reading --------------------------------------------------------------

def _first(record: dict, keys):
    for key in keys:
        if key in record:
            return record[key]
    return None


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _whole(value) -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return int(round(number))


def parse_candidates(data) -> tuple:
    """(candidates, malformed) from the decoded JSON: a list of records,
    or an object holding one under a known name."""
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ValueError("not a list of chips, and no list inside")
    if not isinstance(data, list):
        raise ValueError("not a list of chips")
    candidates, malformed = [], 0
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            malformed += 1
            continue
        image = _first(record, _IMAGE_KEYS)
        x = _number(_first(record, _X_KEYS))
        y = _number(_first(record, _Y_KEYS))
        predicted = _first(record, _CLASS_KEYS)
        score = _number(_first(record, _SCORE_KEYS))
        if (not isinstance(image, str) or not image.strip() or x is None
                or y is None or not isinstance(predicted, str)
                or not predicted.strip()):
            malformed += 1
            continue
        candidates.append(Candidate(
            index=index, image=image.strip(), x=x, y=y,
            image_width=_whole(_first(record, _WIDTH_KEYS)),
            image_height=_whole(_first(record, _HEIGHT_KEYS)),
            predicted=predicted.strip(),
            score=0.0 if score is None else score))
    return candidates, malformed


def read_candidates(path) -> tuple:
    """(candidates, malformed) from a JSON file. Raises ValueError for a
    file that is not a list of chips, OSError/JSONDecodeError as usual."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return parse_candidates(data)


# -- matching -------------------------------------------------------------

def _tail_overlap(local_path: str, given: str) -> int:
    """How many folder names, counted from the file inward, the two
    paths share."""
    local = str(local_path).replace("\\", "/").split("/")
    other = str(given).replace("\\", "/").split("/")
    shared = 0
    for a, b in zip(reversed(local[:-1]), reversed(other[:-1])):
        if a.lower() != b.lower():
            break
        shared += 1
    return shared


def file_size(path) -> tuple | None:
    """(width, height) read from the raster's header, or None."""
    try:
        with gdal_config.opened(path) as src:
            return int(src.width), int(src.height)
    except Exception as exc:                   # noqa: BLE001 - unreadable
        debug(f"model review: cannot read {path}: {type(exc).__name__}: "
              f"{exc}")
        return None


def _image_size(image, size_of) -> tuple | None:
    if image.original_width and image.original_height:
        return int(image.original_width), int(image.original_height)
    size = size_of(image.path)
    if size is not None:
        # Remembered, as Add Directory would have: the next read is free.
        image.original_width, image.original_height = size
    return size


def match_candidates(candidates, project, malformed: int = 0,
                     size_of=file_size) -> tuple:
    """(chips, report): every candidate the project can take, placed on
    its image at that image's resolution. ``size_of(path)`` reads a
    raster's size when the project did not record it."""
    report = MatchReport(total=len(candidates), malformed=malformed)
    classes = set(project.classes)
    by_name: dict = {}
    for path, image in project.images.items():
        by_name.setdefault(image_key(image.path or path), []).append(
            (path, image))
    chips = []
    for candidate in candidates:
        if candidate.predicted not in classes:
            report.unknown_class[candidate.predicted] += 1
            continue
        found = by_name.get(image_key(candidate.image), [])
        if not found:
            report.no_image[candidate.name] += 1
            continue
        if len(found) > 1:
            scores = {path: _tail_overlap(path, candidate.image)
                      for path, _image in found}
            best = max(scores.values())
            found = [(path, image) for path, image in found
                     if scores[path] == best]
            if len(found) > 1 or best == 0:
                report.ambiguous[candidate.name] += 1
                continue
        path, image = found[0]
        size = _image_size(image, size_of)
        if size is None:
            report.unreadable[candidate.name] += 1
            continue
        width, height = size
        px, py, rescaled = candidate.x, candidate.y, False
        if (candidate.image_width and candidate.image_height
                and (candidate.image_width, candidate.image_height)
                != (width, height)):
            px = candidate.x * width / candidate.image_width
            py = candidate.y * height / candidate.image_height
            rescaled = True
        # Inside the image: 0 <= x < width is what every reader needs.
        px = min(max(px, 0.0), width - 1e-6)
        py = min(max(py, 0.0), height - 1e-6)
        chips.append(Chip(key=candidate.index, image_path=path,
                          image_name=Path(str(image.path or path).replace(
                              "\\", "/")).name,
                          group=image.group or "", pixel_x=px, pixel_y=py,
                          predicted=candidate.predicted,
                          score=candidate.score, rescaled=rescaled))
        report.matched += 1
    return chips, report


def by_class(chips) -> dict:
    """The chips by predicted class, each list from most to least sure
    (ties in the file's order)."""
    grouped: dict = {}
    for chip in sorted(chips, key=lambda c: (-c.score, c.key)):
        grouped.setdefault(chip.predicted, []).append(chip)
    return grouped
