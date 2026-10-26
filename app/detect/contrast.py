"""The local-contrast detector: what stands out from its surroundings.

The CFAR family (constant false alarm rate), as radar and sonar
processing has used it for decades: a pixel is judged against the
background round it, not against the whole image, so a vessel on a
bright shoal and a rock on dark mud are found by the same rule. The
background and its spread are the median and the median absolute
deviation of each block of the image - a block is many objects across,
so an object cannot pull them - and every pixel's deviation from the
background in units of that spread is its strength in sigmas. A little
smoothing, matched to the smallest object, takes the speckle out before
the peaks are picked. Any shape: a bar, a disc, a ragged patch.
"""
from __future__ import annotations

import numpy as np

from . import filters
from .base import (BRIGHT, DARK, POLARITY, Candidate, Detector, Parameter,
                   one_per_patch, patch_of)


class Contrast(Detector):
    key = "contrast"
    title = "Local contrast (CFAR)"
    summary = (
        "Finds whatever stands out from the ground round it, bright or dark, "
        "of any shape. Each pixel is compared with the background of its own "
        "neighbourhood - the typical value there, and how much that varies - "
        "and its strength is how many spreads it lies from that typical "
        "value. The sensitivity is the strength a peak must reach. Good "
        "first choice for sonar, radar and optical alike: it assumes nothing "
        "about the object but that it differs from what surrounds it.")
    PARAMETERS = (
        Parameter(
            "background", "Background window", 6.0,
            "How wide a patch the background is judged over, as a multiple "
            "of the largest object size. Raise it where the ground is even "
            "and objects are sparse: broad patches then count as objects "
            "too, so there are more candidates. Lower it where the ground "
            "changes quickly - slopes, textures, a shoreline - so only what "
            "stands out from its immediate surroundings is found: fewer "
            "candidates, and none wider than the window.",
            low=2.0, high=20.0, step=1.0, unit="x largest size"),
        Parameter(
            "smoothing", "Smoothing", 0.5,
            "How much the image is smoothed before peaks are picked, as a "
            "fraction of the smallest object size. Raise it for fewer "
            "candidates: speckle and single bright pixels average away. Too "
            "high and the smallest objects blur into the background and are "
            "missed; at 1 an object of the smallest size is smoothed over "
            "its own width.",
            low=0.0, high=1.0, step=0.1),
        POLARITY,
    )

    def detect(self, values, mask, smallest, largest, floor):
        block = max(8, int(round(self.params["background"] * largest)))
        background, spread = filters.block_median_mad(values, mask, block)
        with np.errstate(invalid="ignore"):
            z = (values - background) / spread
        z = np.where(mask, z, np.nan).astype(np.float32)
        sigma = self.params["smoothing"] * smallest / 2.0
        if sigma >= 0.5:
            z = filters.gaussian_blur(z, sigma, mask)
        polarity = self.params["polarity"]
        if polarity == BRIGHT:
            response = z
        elif polarity == DARK:
            response = -z
        else:
            response = np.abs(z)
        radius = max(1, int(round(smallest / 2.0)))
        # A box blur normalised by its count does not ring at a nodata
        # edge, so peaks may lie right up to one (unlike blob.py's).
        rows, cols = filters.local_maxima(response, radius, floor)
        above = response >= floor / 2.0
        limit = max(2, int(round(largest)))
        found = []
        for row, col in zip(rows.tolist(), cols.tolist()):
            strength = float(response[row, col])
            x, y, size, box = patch_of(above, row, col, limit)
            sign = BRIGHT if z[row, col] >= 0 else DARK
            found.append(Candidate(x, y, strength, size, sign, box))
        return one_per_patch(found)
