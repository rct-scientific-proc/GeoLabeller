"""What the detectors share: a parameter that documents itself, a
candidate, and the detector protocol.

Every parameter says in its ``doc`` what raising it does - more
candidates or fewer, and why - because that line is what the dialog, the
tooltips and the How-To show the user. A test holds each one to it.

Qt-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Parameter:
    """One control of a detector."""
    key: str
    label: str
    default: float | str
    doc: str                    # what it is, and what raising it does
    low: float = 0.0
    high: float = 1.0
    step: float = 1.0
    unit: str = ""
    choices: tuple = ()         # a choice, not a number, when given

    def clamp(self, value):
        if self.choices:
            return value if value in self.choices else self.default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return self.default
        if number != number:        # NaN
            return self.default
        return min(max(number, self.low), self.high)


@dataclass
class Candidate:
    """One place a detector proposes, in the pixels of the array it
    looked at: the middle of the patch round the peak, the peak's
    strength, the patch's size and its box."""
    x: float                    # column of the patch's middle
    y: float                    # row
    strength: float             # robust sigmas the peak stands out by
    size: float                 # across, in those pixels
    polarity: str               # "bright" or "dark"
    box: tuple = ()             # (x0, y0, x1, y1), x1 and y1 exclusive


def one_per_patch(found: list) -> list:
    """The strongest candidate of each patch: a flat-topped object has a
    peak at every pixel of its top, and they are all the one patch."""
    best: dict = {}
    for candidate in found:
        key = candidate.box or (candidate.x, candidate.y)
        held = best.get(key)
        if held is None or candidate.strength > held.strength:
            best[key] = candidate
    return list(best.values())


def patch_of(above, row: int, col: int, limit: int) -> tuple:
    """(x, y, size, box) of the patch containing the peak at (row, col):
    its middle, its diameter as a disc, and its box - see
    filters.region."""
    from . import filters
    count, box = filters.region(above, row, col, limit)
    x0, y0, x1, y1 = box
    return ((x0 + x1 - 1) / 2.0, (y0 + y1 - 1) / 2.0,
            filters.diameter_of(count), box)


BRIGHT, DARK, BOTH = "bright", "dark", "both"
POLARITY = Parameter(
    "polarity", "Look for", BOTH,
    "Patches brighter than their surroundings, darker, or both. Bright "
    "alone or dark alone gives fewer candidates: a wreck's shadow is dark, "
    "a vessel's return bright, and clutter comes in both.",
    choices=(BOTH, BRIGHT, DARK))


def score_of(strength: float) -> float:
    """A strength in sigmas as the 0..1 score the review grid shows:
    half at eight sigmas, three quarters at twenty-four, never one."""
    s = max(0.0, float(strength))
    return s / (s + 8.0)


class Detector:
    """A detector: ``detect`` returns every candidate at least ``floor``
    sigmas strong, in the array's own pixels. Subclasses set ``key``,
    ``title``, ``summary`` and ``PARAMETERS``."""

    key = ""
    title = ""
    summary = ""
    PARAMETERS: tuple = ()

    def __init__(self, **params):
        self.params = {p.key: p.clamp(params.get(p.key, p.default))
                       for p in self.PARAMETERS}

    @classmethod
    def defaults(cls) -> dict:
        return {p.key: p.default for p in cls.PARAMETERS}

    @classmethod
    def describe(cls) -> str:
        """The user documentation: what it does, then each control and
        what raising it does."""
        lines = [cls.summary.strip(), ""]
        for p in cls.PARAMETERS:
            unit = f" ({p.unit})" if p.unit else ""
            if p.choices:
                default = f"default {p.default}"
            else:
                default = f"default {p.default:g}, {p.low:g} to {p.high:g}"
            lines.append(f"{p.label}{unit} - {default}. {p.doc.strip()}")
        return "\n".join(lines)

    def detect(self, values: np.ndarray, mask: np.ndarray, smallest: float,
               largest: float, floor: float) -> list:
        """Candidates on ``values`` (float32, NaN where there is no data;
        ``mask`` True where there is) of ``smallest`` to ``largest``
        pixels across, at least ``floor`` sigmas strong."""
        raise NotImplementedError


@dataclass
class Peaks:
    """What a detector found before the size filter, kept so a sweep of
    sensitivities costs one pass: see evaluate.py."""
    candidates: list = field(default_factory=list)
