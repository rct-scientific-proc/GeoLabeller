"""The numpy primitives the detectors are built from.

All O(N) in the pixels and nodata-aware: a pixel with no data takes no
part in a mean and is never a candidate. Box blurs come from an integral
image; a Gaussian is three box blurs; the background and its spread are
the median and the median absolute deviation of each block of the image,
which an object cannot pull (a block is many objects across); local
maxima come from two one-dimensional sliding maxima. Numpy only: scipy
would add forty megabytes to the installer for four functions.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

MAD_TO_SIGMA = 1.4826           # the MAD of a normal is sigma / 1.4826


def _integral(a: np.ndarray) -> np.ndarray:
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    out[1:, 1:] = np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1)
    return out


def window_sum(a: np.ndarray, radius: int) -> np.ndarray:
    """The sum over the (2r + 1) square window round each pixel, the
    image taken as zero beyond its edges."""
    r = int(radius)
    if r <= 0:
        return a.astype(np.float64)
    h, w = a.shape
    padded = np.zeros((h + 2 * r, w + 2 * r), dtype=np.float64)
    padded[r:r + h, r:r + w] = a
    total = _integral(padded)
    k = 2 * r + 1
    return (total[k:, k:] - total[:h, k:] - total[k:, :w] + total[:h, :w])


def box_blur(values: np.ndarray, radius: int,
             mask: "np.ndarray | None" = None) -> np.ndarray:
    """The mean over the window, pixels without data left out; a pixel
    with none in reach comes back NaN."""
    r = int(radius)
    if mask is None:
        mask = np.isfinite(values)
    filled = np.where(mask, values, 0.0)
    total = window_sum(filled, r)
    count = window_sum(mask.astype(np.float64), r)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(count > 0, total / np.maximum(count, 1e-9), np.nan)
    return out.astype(np.float32)


def gaussian_blur(values: np.ndarray, sigma: float,
                  mask: "np.ndarray | None" = None) -> np.ndarray:
    """A Gaussian blur of ``sigma`` pixels as three box blurs (their
    widths from the variance they add up to), nodata left out of the
    first pass and left NaN at the end."""
    if sigma < 0.5:
        return values.astype(np.float32)
    if mask is None:
        mask = np.isfinite(values)
    width = math.sqrt(12.0 * sigma * sigma / 3.0 + 1.0)
    r = max(1, int(round((width - 1.0) / 2.0)))
    out = box_blur(values, r, mask)
    for _ in range(2):
        out = box_blur(out, r)
    out[~mask] = np.nan
    return out


def _fast_size(n: int) -> int:
    """A length at or above ``n`` the FFT likes: a multiple of 64."""
    return int(-(-n // 64) * 64)


def gaussian_bank(values: np.ndarray, mask: np.ndarray, sigmas) -> dict:
    """{sigma: blurred} for every sigma given: gaussian_blurs held at
    once. For a few; a ladder of many goes through gaussian_blurs one
    at a time (2.9.1: a bank of fifteen at 3000 px held 600 MB)."""
    return dict(gaussian_blurs(values, mask, sigmas))


def gaussian_blurs(values: np.ndarray, mask: np.ndarray, sigmas):
    """Gaussian blurs of ``values`` at several sigmas from one forward
    FFT, yielded (sigma, blurred float32) in the order given. Pixels
    without data count for nothing (the blur is normalised by the
    blurred mask, so a patch by a nodata edge or the image's own edge
    keeps its height), and the image is zero-padded by three of the
    widest sigma so nothing wraps round. True Gaussians, and the
    forward transform shared, which is what makes a ladder of a
    dozen sizes affordable."""
    sigmas = [float(s) for s in sigmas]
    if not sigmas:
        return
    h, w = values.shape
    pad = int(math.ceil(3.0 * max(sigmas)))
    big_h, big_w = _fast_size(h + 2 * pad), _fast_size(w + 2 * pad)
    canvas = np.zeros((big_h, big_w), dtype=np.float64)
    canvas[pad:pad + h, pad:pad + w] = np.where(mask, values, 0.0)
    spectrum = np.fft.rfft2(canvas)
    # The weight's blur - what normalises a patch by an edge - cost a
    # second inverse transform per sigma, half the ladder's time. An
    # image with no holes has a weight that is a plain rectangle, and
    # a Gaussian separates: its blur is the outer product of the blurs
    # of the rows' and the columns' indicators, two one-dimensional
    # transforms each (2.9.4). An image with nodata inside keeps the
    # full transform.
    whole = bool(mask.all())
    if whole:
        rows = np.zeros(big_h, dtype=np.float64)
        rows[pad:pad + h] = 1.0
        cols = np.zeros(big_w, dtype=np.float64)
        cols[pad:pad + w] = 1.0
        rows_spectrum = np.fft.rfft(rows)
        cols_spectrum = np.fft.rfft(cols)
        fy_r = np.fft.rfftfreq(big_h)
    else:
        weight = np.zeros((big_h, big_w), dtype=np.float64)
        weight[pad:pad + h, pad:pad + w] = mask
        weight_spectrum = np.fft.rfft2(weight)
    fy = np.fft.fftfreq(big_h)
    fx = np.fft.rfftfreq(big_w)
    for sigma in sigmas:
        c = -2.0 * math.pi * math.pi * sigma * sigma
        # The Gaussian separates in frequency too: two short exponentials
        # and a product, not an exponential per cell of the grid.
        ky = np.exp(c * fy * fy)
        kx = np.exp(c * fx * fx)
        kernel = ky[:, None] * kx[None, :]
        blurred = np.fft.irfft2(spectrum * kernel, s=(big_h, big_w))
        blurred = blurred[pad:pad + h, pad:pad + w]
        if whole:
            seen_y = np.fft.irfft(rows_spectrum * np.exp(c * fy_r * fy_r),
                                  n=big_h)[pad:pad + h]
            seen_x = np.fft.irfft(cols_spectrum * kx, n=big_w)[pad:pad + w]
            seen = seen_y[:, None] * seen_x[None, :]
        else:
            seen = np.fft.irfft2(weight_spectrum * kernel, s=(big_h, big_w))
            seen = seen[pad:pad + h, pad:pad + w]
        with np.errstate(invalid="ignore", divide="ignore"):
            result = np.where(seen > 1e-3, blurred / np.maximum(seen, 1e-3),
                              np.nan)
        result[~mask] = np.nan
        yield sigma, result.astype(np.float32)


def block_median_mad(values: np.ndarray, mask: np.ndarray,
                     block: int) -> tuple:
    """(background, spread): the median and 1.4826 x the median absolute
    deviation of each ``block`` x ``block`` tile, both as full-size maps
    smoothed across the tile edges. Tiles without data take the image's
    own; the spread is floored at a quarter of the image's, so a flat
    patch does not make every ripple a mountain."""
    h, w = values.shape
    b = max(4, int(block))
    nh, nw = -(-h // b), -(-w // b)
    padded = np.full((nh * b, nw * b), np.nan, dtype=np.float32)
    padded[:h, :w] = np.where(mask, values, np.nan)
    # One row per tile, its pixels along it: a median over one axis.
    # Over two axes at once numpy's nanmedian takes a path six times
    # slower, 1.2 s of every image's time (2.9.4).
    tiles = (padded.reshape(nh, b, nw, b).transpose(0, 2, 1, 3)
             .reshape(nh * nw, b * b))
    with np.errstate(all="ignore"), warnings.catch_warnings():
        # A tile of nothing but nodata is an empty slice: its median
        # is NaN, filled below, and not a warning on the console.
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(tiles, axis=1)
        dev = np.abs(tiles - med[:, None])
        mad = np.nanmedian(dev, axis=1) * MAD_TO_SIGMA
        med = med.reshape(nh, nw)
        mad = mad.reshape(nh, nw)
        whole = np.nanmedian(padded)
        whole_mad = np.nanmedian(np.abs(padded - whole)) * MAD_TO_SIGMA
    if not np.isfinite(whole):
        whole, whole_mad = 0.0, 1.0
    if not np.isfinite(whole_mad) or whole_mad <= 0:
        whole_mad = max(float(np.nanstd(padded)) if np.isfinite(
            np.nanstd(padded)) else 1.0, 1e-6)
    med = np.where(np.isfinite(med), med, whole)
    floor = max(whole_mad * 0.25, 1e-6)
    mad = np.where(np.isfinite(mad) & (mad > 0), mad, whole_mad)
    mad = np.maximum(mad, floor)
    # Across the tile edges: a box blur half a tile wide - and no wider
    # than a quarter of the image (2.9.1). One tile is one value:
    # nothing to blur.
    radius = min(b // 2, max(h, w) // 4) if nh * nw > 1 else 0
    background = tile_blur(med, b, h, w, radius)
    spread = tile_blur(mad, b, h, w, radius)
    return background, np.maximum(spread, floor)


def _tile_overlaps(n: int, b: int, count: int, radius: int) -> np.ndarray:
    """(n, count): for each pixel along one axis, the share of its
    window - ``radius`` each way, cut at the image - that lies in each
    of the ``count`` tiles of ``b`` pixels (the last cut at the image).
    The rows sum to one."""
    pixels = np.arange(n)
    lo = np.maximum(pixels - radius, 0)[:, None]
    hi = np.minimum(pixels + radius, n - 1)[:, None]
    tiles = np.arange(count)
    tile_lo = (tiles * b)[None, :]
    tile_hi = np.minimum((tiles + 1) * b, n)[None, :] - 1
    overlap = np.clip(np.minimum(hi, tile_hi) - np.maximum(lo, tile_lo) + 1,
                      0, None).astype(np.float64)
    return overlap / overlap.sum(axis=1, keepdims=True)


def tile_blur(tiles: np.ndarray, b: int, h: int, w: int,
              radius: int) -> np.ndarray:
    """The (h, w) map that is ``tiles`` spread over its ``b``-pixel
    tiles and box-blurred ``radius`` each way (the window cut at the
    image's edges, as box_blur cuts it): exactly that, found as a
    weighted sum of the few tiles each window overlaps instead of a
    blur over the full-size map (2.9.4)."""
    tiles = np.asarray(tiles, dtype=np.float64)
    nh, nw = tiles.shape
    rows = _tile_overlaps(h, b, nh, max(0, int(radius)))
    cols = _tile_overlaps(w, b, nw, max(0, int(radius)))
    return ((rows @ tiles) @ cols.T).astype(np.float32)


def local_maxima(values: np.ndarray, radius: int, floor: float) -> tuple:
    """(rows, cols) of the pixels at least ``floor`` that are the largest
    within ``radius``; NaN is never one. Two one-dimensional sliding
    maxima, so a window costs its width, not its area."""
    r = max(1, int(radius))
    a = np.where(np.isfinite(values), values, -np.inf).astype(np.float32)
    padded = np.pad(a, r, mode="constant", constant_values=-np.inf)
    rows = sliding_window_view(padded, 2 * r + 1, axis=0).max(axis=-1)
    both = sliding_window_view(rows, 2 * r + 1, axis=1).max(axis=-1)
    peak = (a >= floor) & (a == both) & np.isfinite(a)
    return np.nonzero(peak)


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """True where every pixel within ``radius`` has data: the band along
    a nodata edge, where a blur's two normalisations disagree and ring,
    is no place to pick a peak."""
    r = int(radius)
    if r <= 0:
        return mask
    missing = window_sum((~mask).astype(np.float64), r)
    return mask & (missing < 0.5)


def region(above: np.ndarray, row: int, col: int, limit: int) -> tuple:
    """(count, (x0, y0, x1, y1)): how many pixels of the True region
    containing (row, col) lie within ``limit`` of it each way, and
    their box (x1, y1 exclusive): a bounded flood fill, so a patch
    broader than any object is cut at the box and told by its size."""
    h, w = above.shape
    r0, r1 = max(0, row - limit), min(h, row + limit + 1)
    c0, c1 = max(0, col - limit), min(w, col + limit + 1)
    window = above[r0:r1, c0:c1]
    if not window[row - r0, col - c0]:
        return 1, (col, row, col + 1, row + 1)
    seen = np.zeros(window.shape, dtype=bool)
    stack = [(row - r0, col - c0)]
    seen[row - r0, col - c0] = True
    count = 0
    hh, ww = window.shape
    lo_r = hi_r = row - r0
    lo_c = hi_c = col - c0
    while stack:
        rr, cc = stack.pop()
        count += 1
        lo_r, hi_r = min(lo_r, rr), max(hi_r, rr)
        lo_c, hi_c = min(lo_c, cc), max(hi_c, cc)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = rr + dr, cc + dc
            if 0 <= nr < hh and 0 <= nc < ww and not seen[nr, nc] \
                    and window[nr, nc]:
                seen[nr, nc] = True
                stack.append((nr, nc))
    return count, (c0 + lo_c, r0 + lo_r, c0 + hi_c + 1, r0 + hi_r + 1)


def diameter_of(count: int) -> float:
    """The diameter of a disc of this many pixels."""
    return 2.0 * math.sqrt(max(count, 1) / math.pi)


def decimated_shape(height: int, width: int, factor: float) -> tuple:
    return (max(1, int(round(height / factor))),
            max(1, int(round(width / factor))))
