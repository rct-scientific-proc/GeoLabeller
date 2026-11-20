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
import os
import threading
import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

MAD_TO_SIGMA = 1.4826           # the MAD of a normal is sigma / 1.4826


def _integral(a: np.ndarray) -> np.ndarray:
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    out[1:, 1:] = np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1)
    return out


SHIFTED_SUM_MAX = 4             # radii summed by shifted copies


def _shifted_window_sum(a: np.ndarray, r: int) -> np.ndarray:
    """window_sum for a small radius: 2r + 1 shifted copies added along
    each axis. An integral image is two cumulative sums over a padded
    float64 copy whatever the radius; at radius 1 - the Contrast
    detector's smoothing, at the working scale's six-pixel smallest
    object - this is twenty times quicker (1.1 s against 0.06 s for its
    three box blurs at 2,400 px; 2026-11-20)."""
    a = np.asarray(a, dtype=np.float64)
    rows = a.copy()
    for d in range(1, r + 1):
        rows[d:] += a[:-d]
        rows[:-d] += a[d:]
    out = rows.copy()
    for d in range(1, r + 1):
        out[:, d:] += rows[:, :-d]
        out[:, :-d] += rows[:, d:]
    return out


def window_sum(a: np.ndarray, radius: int) -> np.ndarray:
    """The sum over the (2r + 1) square window round each pixel, the
    image taken as zero beyond its edges."""
    r = int(radius)
    if r <= 0:
        return a.astype(np.float64)
    if r <= SHIFTED_SUM_MAX:
        return _shifted_window_sum(a, r)
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
        coarse = _CoarseWeight(weight, pad, h, w)
    fy = np.fft.fftfreq(big_h)
    fx = np.fft.rfftfreq(big_w)

    def one(sigma):
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
        elif coarse.step(sigma) > 1:
            seen = coarse.blur(sigma)
        else:
            seen = np.fft.irfft2(weight_spectrum * kernel, s=(big_h, big_w))
            seen = seen[pad:pad + h, pad:pad + w]
        with np.errstate(invalid="ignore", divide="ignore"):
            result = np.where(seen > 1e-3, blurred / np.maximum(seen, 1e-3),
                              np.nan)
        result[~mask] = np.nan
        return sigma, result.astype(np.float32)

    # The sigmas are independent given the one forward transform, and
    # numpy's FFTs let go of the interpreter lock: several at once run in
    # parallel (eight took 1.04 s one after another, 0.16 s on eight
    # threads; 2026-11-20). As many at a time as memory allows - a
    # 3,000 px working image pads to a canvas where one sigma in flight
    # holds over half a gigabyte - and in order, a batch at a time, so a
    # ladder's caller still sees them one by one.
    workers = fft_workers(big_h * big_w)
    for start in range(0, len(sigmas), workers):
        yield from _in_threads(one, sigmas[start:start + workers])


# -- running the ladder's sigmas side by side (2026-11-20) -------------------

FFT_MEMORY_BUDGET = 1_200_000_000   # bytes the sigmas in flight may hold
# What one sigma in flight holds per canvas cell, measured with
# tracemalloc at 2,816 px: 30.2 B (no nodata), 32.4 B (nodata inside).
FFT_BYTES_PER_CELL = 32
FFT_MAX_WORKERS = 4


_sharing = 1                    # images being searched at once (run.py)


def fft_workers(cells: int) -> int:
    """How many sigmas to transform at once on a canvas of ``cells``:
    the cores there are, at most FFT_MAX_WORKERS, within the budget -
    the budget split between the images run.py searches side by side."""
    budget = FFT_MEMORY_BUDGET // max(1, _sharing)
    by_memory = budget // max(1, cells * FFT_BYTES_PER_CELL)
    return int(max(1, min(FFT_MAX_WORKERS, os.cpu_count() or 1,
                          by_memory)))


class sharing_budget:
    """``with sharing_budget(n):`` - n images are being searched at once,
    so each gets 1/n of FFT_MEMORY_BUDGET."""

    def __init__(self, images: int):
        self.images = max(1, int(images))

    def __enter__(self):
        global _sharing
        self._before, _sharing = _sharing, self.images
        return self

    def __exit__(self, *_exc):
        global _sharing
        _sharing = self._before
        return False


def _in_threads(fn, items: list) -> list:
    """[fn(item) for item in items], one thread per item, in order; the
    first error any raised is raised here. Threads, not a pool: the
    installed build leaves concurrent.futures out (as snippet_store.py
    found)."""
    if len(items) <= 1:
        return [fn(item) for item in items]
    results: list = [None] * len(items)
    errors: list = []

    def work(index, item):
        try:
            results[index] = fn(item)
        except BaseException as exc:  # noqa: BLE001 - raised below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i, item), daemon=True,
                                name="detect-fft")
               for i, item in enumerate(items)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    return results


# -- the nodata weight's blur on a coarser grid (2026-11-20) -----------------

COARSE_STEP_PER_SIGMA = 4.0     # the grid step at most sigma / this
COARSE_STEP_MAX = 64


class _CoarseWeight:
    """The blur of the "where is there data" weight for wide sigmas,
    worked on a grid ``step`` times coarser and brought back by linear
    interpolation.

    On an image with nodata in it, every sigma of a ladder paid a second
    full-size inverse transform just for the weight that normalises the
    blur by a nodata edge: a third of Blob's time on a collared image.
    The weight is a 0/1 mask, and its blur at a wide sigma is smooth on
    the scale of that sigma, so a grid ``step`` = the largest power of
    two at most sigma / 4 holds it: its block means are blurred by the
    Gaussian less the block's own variance ((step^2 - 1) / 12), and the
    result read back at each pixel's place. Narrow sigmas, where the
    weight changes within a few pixels of an edge, keep the full
    transform. Measured against the exact blur on a corner wedge, holes
    and a rotated collar (docs_workspace check_coarse_weight.py): the
    weight is off by at most 0.0043, so where there is data (weight
    over a half) a blurred value is off by at most 0.64% - at nodata
    edges only; away from them the weight is 1 either way. The error
    falls as the square of the step: sigma / 2 was 0.017 (2.4%), which
    is too much.
    """

    def __init__(self, weight: np.ndarray, pad: int, h: int, w: int):
        self.weight, self.pad, self.h, self.w = weight, pad, h, w
        self._spectra: dict = {}

    @staticmethod
    def step(sigma: float) -> int:
        """The grid's step for ``sigma``: 1 - the exact, full-size
        transform - below 8 px, where a step of 2 would be more than a
        quarter of the sigma."""
        step = 1
        while (step * 2 <= sigma / COARSE_STEP_PER_SIGMA
               and step * 2 <= COARSE_STEP_MAX):
            step *= 2
        return step

    def _spectrum(self, step: int):
        held = self._spectra.get(step)
        if held is None:
            big_h, big_w = self.weight.shape        # multiples of 64
            blocks = self.weight.reshape(big_h // step, step,
                                         big_w // step, step).mean(
                                             axis=(1, 3))
            held = self._spectra[step] = (np.fft.rfft2(blocks), blocks.shape)
        return held

    def blur(self, sigma: float) -> np.ndarray:
        step = self.step(sigma)
        spectrum, (ch, cw) = self._spectrum(step)
        # The block mean already blurred by a box of ``step``: the rest.
        rest = math.sqrt(max(sigma * sigma - (step * step - 1) / 12.0,
                             0.25)) / step
        c = -2.0 * math.pi * math.pi * rest * rest
        fy = np.fft.fftfreq(ch)
        fx = np.fft.rfftfreq(cw)
        kernel = np.exp(c * fy * fy)[:, None] * np.exp(c * fx * fx)[None, :]
        small = np.fft.irfft2(spectrum * kernel, s=(ch, cw))
        # A canvas pixel p lies at (p - (step - 1) / 2) / step in blocks.
        offset = (step - 1) / 2.0
        rows = (np.arange(self.pad, self.pad + self.h) - offset) / step
        cols = (np.arange(self.pad, self.pad + self.w) - offset) / step
        return _bilinear(small, rows, cols)


def _bilinear(grid: np.ndarray, rows: np.ndarray, cols: np.ndarray):
    """``grid`` read at fractional (rows x cols), one axis at a time."""
    r0 = np.clip(np.floor(rows).astype(np.int64), 0, grid.shape[0] - 2)
    tr = np.clip(rows - r0, 0.0, 1.0)[:, None]
    c0 = np.clip(np.floor(cols).astype(np.int64), 0, grid.shape[1] - 2)
    tc = np.clip(cols - c0, 0.0, 1.0)[None, :]
    by_row = grid[r0] * (1.0 - tr) + grid[r0 + 1] * tr
    return by_row[:, c0] * (1.0 - tc) + by_row[:, c0 + 1] * tc


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
