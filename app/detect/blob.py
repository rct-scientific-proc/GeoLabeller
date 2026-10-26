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
from .base import (BOTH, BRIGHT, DARK, POLARITY, Candidate, Detector,
                   Parameter, one_per_patch, patch_of)

DOG_RATIO = 1.6                 # the wider Gaussian, about: Lowe's ratio
DISC_TO_SIGMA = 2.0 * math.sqrt(2.0)   # a disc's diameter in DoG sigmas
NORM_SIGMA = 8.0                # the response is one shape past this
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
    disc, through the same blurs, so the two agree. The response is
    the same shape at every sigma past a few pixels, so a wide one is
    measured at NORM_SIGMA: measured at its own, a 500 px sigma took
    a 50,000 px canvas (2.9.1)."""
    sigma = min(float(sigma), NORM_SIGMA)
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
        polarity = self.params["polarity"]
        signs = [s for s in (BRIGHT, DARK)
                 if polarity == BOTH or polarity == s]
        # The strongest response over the sizes, per sign: looking for
        # both on one map, a shadow's maximum lost to a brighter
        # neighbour's within the window (2.9.1).
        best = {s: np.full(values.shape, -np.inf, dtype=np.float32)
                for s in signs}
        # The blurs come one at a time, each the narrow of one size and
        # the wide of the size ``wider`` rungs below, so only that many
        # are held: a whole bank at 3000 px was 600 MB.
        held: dict = {}
        smooth = None
        for j, (wide_sigma, blurred) in enumerate(
                filters.gaussian_blurs(z, mask, sigmas)):
            held[j] = blurred
            if smooth is None:
                smooth = blurred            # the narrowest, for the patch
            if j < wider:
                continue
            i = j - wider
            narrow = sigmas[i]
            response = (held.pop(i) - blurred) / _norm(narrow,
                                                       wide_sigma / narrow)
            for sign in signs:
                signed = response if sign == BRIGHT else -response
                signed = np.where(mask & np.isfinite(signed), signed, -np.inf)
                best[sign] = np.maximum(best[sign], signed)
        radius = max(1, int(round(smallest / 2.0)))
        # No peak within the narrowest blur of a nodata edge, where the
        # two blurs' normalisations disagree and ring.
        inside = filters.erode(mask, max(1, int(round(sigmas[0]))))
        limit = max(2, int(round(largest)))
        found = []
        for sign in signs:
            own = np.where(inside & np.isfinite(best[sign]) & (best[sign] > 0),
                           best[sign], np.nan).astype(np.float32)
            rows, cols = filters.local_maxima(own, radius, floor)
            # The size and the middle from the patch itself: the smoothed
            # deviation of the peak's sign above half the threshold round
            # it, on the narrowest blur; the mask made once per sign.
            with np.errstate(invalid="ignore"):
                above = (smooth if sign == BRIGHT else -smooth) >= floor / 2.0
            for row, col in zip(rows.tolist(), cols.tolist()):
                x, y, size, box = patch_of(above, row, col, limit)
                found.append(Candidate(x, y, float(best[sign][row, col]), size,
                                       sign, box))
        return one_per_patch(found)
