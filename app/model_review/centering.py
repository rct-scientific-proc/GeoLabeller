"""Finding the object in a chip, for Model Review's Auto-center.

The model team's centre is where their classifier's window sat, not
where the clutter is; a label put there sits beside the object. The
user can double-click the object (window.py); this module offers a
first guess by machine, to be looked at, not trusted.

The method is mean shift towards contrast: a chip's background is its
median grey; every pixel's weight is how far it stands from that, in
robust standard deviations (the median absolute deviation), with the
first two deviations forgiven so plain texture weighs nothing. From the
chip's centre, the weighted mean of the pixels within a radius is taken,
the window moves there, and again, until it settles - on the nearest
patch of anything that stands out, bright or dark. Nothing standing out
(no pixel within reach beyond ``MIN_PEAK`` deviations) is the honest
answer None, and the centre is left alone.

Numpy only, no learned model (ruled out for masks 2026-10-02 and kept
to here): it finds compact anomalies on a fairly uniform background -
sonar and SAR clutter on water, the usual false positive - and nothing
cleverer. It will be pulled to the brighter of two neighbours, and to a
hull's shadow and hull together. The centre mark shows where it landed.
"""
from __future__ import annotations

import math

import numpy as np

# Weights are deviations from the background beyond this many, so that
# ordinary texture (within about two sigma) carries no weight at all.
FORGIVEN_SIGMA = 2.0
# Something is there only if some pixel within reach stands this far out
# (after the smoothing, which halves pixel noise).
MIN_PEAK = 3.0
# And carries at least this much weight in all: a few real pixels.
MIN_MASS = 20.0
MAX_STEPS = 30
SETTLED_PX = 0.25


def _smoothed(gray: np.ndarray) -> np.ndarray:
    """A 3 x 3 box blur, edges repeated: pixel noise averaged down
    before anything is judged by it."""
    padded = np.pad(gray, 1, mode="edge")
    out = np.zeros_like(gray)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out += padded[dy:dy + gray.shape[0], dx:dx + gray.shape[1]]
    return out / 9.0


def anomaly_weights(gray: np.ndarray) -> np.ndarray:
    """How much each pixel stands out from the chip's background, in
    robust sigmas beyond the forgiven two; zero for ordinary texture."""
    smooth = _smoothed(np.asarray(gray, dtype=np.float32))
    background = float(np.median(smooth))
    deviation = np.abs(smooth - background)
    sigma = float(np.median(deviation)) * 1.4826
    sigma = max(sigma, 1e-3)
    return np.clip(deviation / sigma - FORGIVEN_SIGMA, 0.0, None)


def find_centre(gray: np.ndarray, start, radius: float) -> "tuple | None":
    """Where the object nearest ``start`` (column, row) is, within the
    chip: a (column, row) pair, or None when nothing stands out.

    ``gray`` is the chip as one band, any dtype. ``radius`` is the mean
    shift window in pixels - a quarter of the chip suits a chip framed
    around one object.
    """
    weights = anomaly_weights(gray)
    if not np.any(weights > 0):
        return None
    rows, cols = np.indices(weights.shape, dtype=np.float32)
    height, width = weights.shape
    x = min(max(float(start[0]), 0.0), width - 1.0)
    y = min(max(float(start[1]), 0.0), height - 1.0)
    radius = max(float(radius), 1.0)
    mass = 0.0
    inside = None
    for _ in range(MAX_STEPS):
        inside = (cols - x) ** 2 + (rows - y) ** 2 <= radius * radius
        local = weights * inside
        mass = float(local.sum())
        if mass <= 0.0:
            return None
        nx = float((local * cols).sum() / mass)
        ny = float((local * rows).sum() / mass)
        moved = math.hypot(nx - x, ny - y)
        x, y = nx, ny
        if moved < SETTLED_PX:
            break
    if mass < MIN_MASS or float((weights * inside).max()) < MIN_PEAK:
        return None
    return x, y
