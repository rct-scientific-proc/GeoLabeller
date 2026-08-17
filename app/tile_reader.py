"""Read one map tile straight from a GeoTIFF, without decoding the whole image.

The canvas has always reprojected an entire image into a single RGBA array and
sliced tiles out of it, so memory grew with the image rather than the screen -
which is why a very large mosaic could not be shown at full resolution. These
functions reproject *one tile at a time*, reading only the source window that
tile covers, so the cost depends on how much is on screen instead.

The destination grid is deliberately identical to the whole-image path: the
same ``calculate_default_transform`` call defines a level's pixel grid, and a
tile is simply a ``TILE_SIZE`` block of it. A tile therefore lands exactly
where the corresponding slice of the whole-image array would have, which is
what makes the two interchangeable (and testable against each other).

Levels are decimation factors, as elsewhere: 1 is full resolution, 4 is every
fourth pixel. Passing a factor the file actually has an overview for keeps the
read exact and cheap, since GDAL then serves it from the pyramid.

Contents:
- ``level_grid`` - the transform and size of a whole level's pixel grid.
- ``tile_span`` / ``tile_transform`` / ``tile_bounds`` - a tile's place in it.
- ``tiles_for_bounds`` - which tiles cover a region.
- ``read_tile`` - the windowed read and reprojection itself.
"""
import math

import numpy as np
import rasterio
from affine import Affine
from rasterio.warp import (Resampling, calculate_default_transform, reproject,
                           transform_bounds)
from rasterio.windows import Window

# Tile edge in destination pixels. Matches the canvas's own TILE_SIZE.
TILE_SIZE = 512

# Source pixels of slack around a tile's window. Bilinear resampling reads one
# pixel beyond each edge, so without this a tile would come out with a hairline
# seam where it meets its neighbour.
EDGE_MARGIN_PX = 4

# Warp with the exact coordinate transformer rather than GDAL's default
# approximation (an error threshold of 0.125 destination pixels).
#
# The approximation is fitted over the destination being written, so the same
# ground reprojected as part of a whole image and as an individual tile comes
# out subtly differently - measured at up to 14 grey levels apart on detailed
# imagery. Whole-image rendering never showed this, but tiles would: two
# neighbours would disagree along the edge they share, drawing a seam. Exact
# transformation removes it (measured difference: zero), and costs little here
# because only the tiles on screen are ever warped.
WARP_TOLERANCE = 0.0


def level_grid_for(src_crs, src_transform, src_width: int, src_height: int,
                   dst_crs, level: int):
    """``(transform, width, height)`` of a level's grid, without an open file.

    The canvas keeps a layer's source CRS, transform and size, so it can work
    out which tiles a view needs - and where they belong - without touching
    the disk. Only the actual pixel read needs the file open.
    """
    level = max(1, int(level))
    bounds = rasterio.transform.array_bounds(
        src_height, src_width, src_transform)
    transform, width, height = calculate_default_transform(
        src_crs, dst_crs, src_width, src_height, *bounds)
    if level > 1:
        transform, width, height = calculate_default_transform(
            src_crs, dst_crs, src_width, src_height, *bounds,
            dst_width=max(1, width // level),
            dst_height=max(1, height // level))
    return transform, width, height


def level_grid(src, dst_crs, level: int):
    """Return ``(transform, width, height)`` of the whole image at ``level``.

    This mirrors the whole-image loader exactly, so tiles cut from this grid
    line up with what that path produces.
    """
    return level_grid_for(src.crs, src.transform, src.width, src.height,
                          dst_crs, level)


def tile_span(width: int, height: int, tx: int, ty: int,
              tile_size: int = TILE_SIZE):
    """Destination pixel box ``(x0, y0, x1, y1)`` of a tile, clipped to the grid.

    Edge tiles come out smaller than ``tile_size``; an out-of-range index gives
    an empty box (``x1 <= x0``).
    """
    x0 = tx * tile_size
    y0 = ty * tile_size
    x1 = min(x0 + tile_size, width)
    y1 = min(y0 + tile_size, height)
    return x0, y0, max(x0, x1), max(y0, y1)


def tile_transform(grid_transform: Affine, x0: int, y0: int) -> Affine:
    """The affine of a tile: the level's grid shifted to the tile's corner."""
    return grid_transform * Affine.translation(x0, y0)


def tile_bounds(grid_transform: Affine, x0: int, y0: int, x1: int, y1: int):
    """``(west, south, east, north)`` of a tile in the destination CRS."""
    west, north = grid_transform * (x0, y0)
    east, south = grid_transform * (x1, y1)
    return west, south, east, north


def tiles_for_bounds(grid_transform: Affine, width: int, height: int,
                     bounds, tile_size: int = TILE_SIZE):
    """Tile indices covering ``bounds`` (west, south, east, north), clipped.

    Used to decide what a viewport needs; returns an empty list when the region
    misses the image entirely.
    """
    west, south, east, north = bounds
    inverse = ~grid_transform
    # Both diagonal corners, since a flipped axis would otherwise invert the range.
    xs, ys = [], []
    for wx, wy in ((west, north), (east, south), (west, south), (east, north)):
        px, py = inverse * (wx, wy)
        xs.append(px)
        ys.append(py)
    x_start = max(0, int(math.floor(min(xs) / tile_size)))
    x_end = min((width - 1) // tile_size, int(math.floor(max(xs) / tile_size)))
    y_start = max(0, int(math.floor(min(ys) / tile_size)))
    y_end = min((height - 1) // tile_size, int(math.floor(max(ys) / tile_size)))
    if x_end < x_start or y_end < y_start:
        return []
    return [(tx, ty)
            for ty in range(y_start, y_end + 1)
            for tx in range(x_start, x_end + 1)]


def _source_window(src, dst_crs, bounds, level: int):
    """Source window covering ``bounds``, plus margin, and its read shape.

    Returns ``(window, out_height, out_width, read_transform)``, or ``None``
    when the tile falls outside the source.

    The work is done in *decimated* pixels - the grid the whole-image loader
    reads with ``out_shape=(height // level, width // level)`` - and the window
    is snapped to whole decimated pixels. That makes a tile's samples exactly a
    sub-grid of the whole-image samples, so both paths resolve to the same
    overview pixels and produce identical values. Snapping in full-resolution
    pixels instead leaves the two grids fractionally out of step, which shows
    up as differences of tens of levels on detailed imagery.
    """
    # The decimated grid the whole-image path uses. Note the scale is
    # width / (width // level), which is only exactly `level` when the level
    # divides the raster evenly - so derive it rather than assuming.
    read_w = max(1, src.width // level)
    read_h = max(1, src.height // level)
    scale_x = src.width / read_w
    scale_y = src.height / read_h
    grid_transform = src.transform * Affine.scale(scale_x, scale_y)

    try:
        src_bounds = transform_bounds(dst_crs, src.crs, *bounds, densify_pts=21)
    except Exception:
        return None
    if not all(math.isfinite(v) for v in src_bounds):
        return None

    # Window in decimated pixels, widened by the margin and snapped outward.
    window = rasterio.windows.from_bounds(*src_bounds, transform=grid_transform)
    col_off = math.floor(window.col_off) - EDGE_MARGIN_PX
    row_off = math.floor(window.row_off) - EDGE_MARGIN_PX
    col_end = math.ceil(window.col_off + window.width) + EDGE_MARGIN_PX
    row_end = math.ceil(window.row_off + window.height) + EDGE_MARGIN_PX

    # Clamp to the decimated grid; a tile over the edge keeps what exists.
    col_off = max(0, min(col_off, read_w))
    row_off = max(0, min(row_off, read_h))
    col_end = max(col_off, min(col_end, read_w))
    row_end = max(row_off, min(row_end, read_h))
    out_w = col_end - col_off
    out_h = row_end - row_off
    if out_w <= 0 or out_h <= 0:
        return None

    # Back to full-resolution pixels for the actual read: the exact span those
    # decimated samples cover, resampled to out_w x out_h.
    read_transform = grid_transform * Affine.translation(col_off, row_off)
    window = Window(col_off * scale_x, row_off * scale_y,
                    out_w * scale_x, out_h * scale_y)
    return window, out_h, out_w, read_transform


def read_tile(src, dst_crs, level: int, tx: int, ty: int,
              tile_size: int = TILE_SIZE, grid=None):
    """Reproject a single tile, reading only the source window it needs.

    Returns an ``(h, w, 4)`` uint8 RGBA array - alpha 0 where the tile has no
    source data - or ``None`` when the tile is outside the image or fully
    empty. ``grid`` may carry a precomputed :func:`level_grid` result, since
    every tile of a level shares it.

    The band handling mirrors the whole-image loader: band 1 goes through
    float32 so nodata (and the blank wedges reprojection leaves at the edges)
    can be detected and turned into transparency, while the remaining bands are
    reprojected directly as uint8.
    """
    level = max(1, int(level))
    grid_transform, width, height = grid if grid is not None else level_grid(
        src, dst_crs, level)

    x0, y0, x1, y1 = tile_span(width, height, tx, ty, tile_size)
    out_h, out_w = y1 - y0, x1 - x0
    if out_h <= 0 or out_w <= 0:
        return None

    dst_transform = tile_transform(grid_transform, x0, y0)
    window_info = _source_window(
        src, dst_crs, tile_bounds(grid_transform, x0, y0, x1, y1), level)
    if window_info is None:
        return None
    window, read_h, read_w, read_transform = window_info

    common = {
        "src_transform": read_transform,
        "src_crs": src.crs,
        "dst_transform": dst_transform,
        "dst_crs": dst_crs,
        "resampling": Resampling.bilinear,
        "tolerance": WARP_TOLERANCE,
    }

    # Band 1 in float32: NaN marks both source nodata and the areas the
    # reprojection never writes, which become the alpha channel.
    band1 = src.read(1, window=window,
                     out_shape=(read_h, read_w)).astype(np.float32)
    if src.nodata is not None:
        band1[band1 == src.nodata] = np.nan
    dst_band1 = np.full((out_h, out_w), np.nan, dtype=np.float32)
    reproject(source=band1, destination=dst_band1,
              src_nodata=np.nan, dst_nodata=np.nan, **common)

    nodata_mask = np.isnan(dst_band1)
    if bool(nodata_mask.all()):
        return None  # nothing of this tile is covered by real data

    rgba = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    rgba[:, :, 0] = np.clip(np.nan_to_num(dst_band1, nan=0.0), 0, 255
                            ).astype(np.uint8)
    del dst_band1

    if src.count >= 3:
        for index, channel in ((2, 1), (3, 2)):
            band = src.read(index, window=window, out_shape=(read_h, read_w))
            if src.nodata is not None:
                band = np.where(band == src.nodata, 0, band)
            band = np.clip(band, 0, 255).astype(np.uint8)
            dst_band = np.zeros((out_h, out_w), dtype=np.uint8)
            reproject(source=band, destination=dst_band,
                      src_nodata=0, dst_nodata=0, **common)
            rgba[:, :, channel] = dst_band
    else:
        rgba[:, :, 1] = rgba[:, :, 0]
        rgba[:, :, 2] = rgba[:, :, 0]

    rgba[:, :, 3] = np.where(nodata_mask, 0, 255).astype(np.uint8)
    return rgba
