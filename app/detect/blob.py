"""The blob detector: compact patches of about the sizes asked for.

A difference of Gaussians at each size between the smallest and the
largest: the image blurred to the size of the object, less the image
blurred wider, answers strongly where a patch of about that size sits
on a different background and weakly to edges, gradients and speckle.
The response is normalised so that a patch one spread above its
background scores one, whatever its size; the size reported is that of
the patch itself round the peak. Round-ish objects of a known size
range - mines, boulders, craters, tanks - are its strength; a long thin
wake or a ragged patch suits the contrast detector better.

The blurs come from one FFT of the image (filters.gaussian_bank): a
dozen sizes cost little more than one.
"""
from __future__ import annotations

import math

import numpy as np

from . import filters
from .base import (BRIGHT, DARK, POLARITY, Candidate, Detector, Parameter,
                   one_per_patch, patch_of)

DOG_RATIO = 1.6                 # the wider Gaussian, about: Lowe's ratio
DISC_TO_SIGMA = 2.0 * math.sqrt(2.0)   # a disc's diameter in DoG sigmas
_NORM: dict = {}                # (sigma, ratio) -> the response to a unit disc


def ladder(smallest: float, largest: float, steps: int) -> tuple:
    """(sizes, sigmas, wider): the sizes tried, ``steps`` to a doubling,
    from the smallest to the largest; each size's Gaussian sigma; and
    how many rungs up the wider Gaussian of its difference sits, so
    that one blur serves as the narrow of one size and the wide of
    another."""
    smallest = max(1.0, float(smallest))
    largest = max(smallest, float(largest))
    steps = max(1, int(steps))
    ratio = 2.0 ** (1.0 / steps)
    sizes = [smallest]
    while sizes[-1] * ratio < largest * 0.999:
        sizes.append(sizes[-1] * ratio)
    if sizes[-1] < largest * 0.999:
        sizes.append(largest)
    wider = max(1, int(round(math.log(DOG_RATIO) / math.log(ratio))))
    sigmas = [max(0.5, size / DISC_TO_SIGMA) for size in sizes]
    for _ in range(wider):
        sigmas.append(sigmas[-1] * ratio)
    return sizes, sigmas, wider


def _norm(sigma: float, ratio: float) -> float:
    """What the difference of Gaussians makes of a disc one unit above
    zero, at the disc's centre: found once per sigma on a synthetic
    disc, through the same blurs, so the two agree."""
    key = (round(sigma, 3), round(ratio, 3))
    if key not in _NORM:
        radius = sigma * DISC_TO_SIGMA / 2.0
        side = int(math.ceil(radius * 2 + sigma * ratio * 8)) | 1
        centre = side // 2
        rows, cols = np.indices((side, side), dtype=np.float32)
        disc = (((rows - centre) ** 2 + (cols - centre) ** 2)
                <= radius * radius).astype(np.float32)
        bank = filters.gaussian_bank(disc, np.ones_like(disc, dtype=bool),
                                     (sigma, sigma * ratio))
        response = bank[sigma] - bank[sigma * ratio]
        _NORM[key] = max(float(response[centre, centre]), 1e-6)
    return _NORM[key]


class Blob(Detector):
    key = "blob"
    title = "Blobs (difference of Gaussians)"
    summary = (
        "Finds compact, round-ish patches of about the sizes you gave, "
        "bright or dark against their background. The image is compared "
        "with itself blurred at each size between the smallest and the "
        "largest; a patch of about that size answers strongly, an edge or a "
        "gradient weakly, and speckle hardly at all. The sensitivity is the "
        "strength a peak must reach, in spreads of the background. Best for "
        "objects of a known size range; a long wake or a ragged patch suits "
        "the contrast detector better.")
    PARAMETERS = (
        Parameter(
            "steps", "Sizes per doubling", 3.0,
            "How many sizes are tried between one size and its double, "
            "from the smallest to the largest. Raise it for a few more "
            "candidates - an object between two sizes answers less to "
            "either - at the cost of time: each size is another pass over "
            "the image.",
            low=1.0, high=6.0, step=1.0),
        POLARITY,
    )

    def detect(self, values, mask, smallest, largest, floor):
        block = max(8, int(round(6.0 * largest)))
        background, spread = filters.block_median_mad(values, mask, block)
        with np.errstate(invalid="ignore"):
            z = (values - background) / spread
        z = np.where(mask, z, np.nan).astype(np.float32)
        sizes, sigmas, wider = ladder(smallest, largest,
                                      int(self.params["steps"]))
        bank = filters.gaussian_bank(z, mask, sigmas)
        polarity = self.params["polarity"]
        best = np.full(values.shape, -np.inf, dtype=np.float32)
        signs = np.zeros(values.shape, dtype=np.int8)
        for i, _size in enumerate(sizes):
            narrow, wide = sigmas[i], sigmas[i + wider]
            response = (bank[narrow] - bank[wide]) / _norm(narrow,
                                                           wide / narrow)
            if polarity == BRIGHT:
                signed = response
            elif polarity == DARK:
                signed = -response
            else:
                signed = np.abs(response)
            signed = np.where(mask & np.isfinite(signed), signed, -np.inf)
            better = signed > best
            best = np.where(better, signed, best)
            signs = np.where(better, np.where(response >= 0, 1, -1), signs)
        best = np.where(np.isfinite(best), best, np.nan).astype(np.float32)
        radius = max(1, int(round(smallest / 2.0)))
        # No peak within the narrowest blur of a nodata edge, where
        # the two blurs' normalisations disagree and ring.
        inside = filters.erode(mask, max(1, int(round(sigmas[0]))))
        peaks = np.where(inside, best, np.nan)
        rows, cols = filters.local_maxima(peaks, radius, floor)
        # The size and the middle from the patch itself: the smoothed
        # deviation above half the threshold round the peak, on the
        # narrowest blur.
        smooth = bank[sigmas[0]]
        limit = max(2, int(round(largest)))
        found = []
        for row, col in zip(rows.tolist(), cols.tolist()):
            sign = 1 if signs[row, col] >= 0 else -1
            above = (smooth * sign) >= floor / 2.0
            x, y, size, box = patch_of(above, row, col, limit)
            found.append(Candidate(x, y, float(best[row, col]), size,
                                   BRIGHT if sign > 0 else DARK, box))
        return one_per_patch(found)
