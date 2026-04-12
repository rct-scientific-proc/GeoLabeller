"""GIM binary file reader.

GIM Binary Format:
    Header: 3 doubles (width, height, channels)
    GCPs: 4 x 4 doubles (pixel_x, pixel_y, lat, lon for each corner)
    Image: width x height x channels float32 values (row-major)
"""
import struct

import numpy as np
from affine import Affine
from rasterio.crs import CRS

from app.readers import registry, ReaderResult, BoundsResult


def read_gim_bounds(file_path: str) -> BoundsResult:
    """Read only the GIM header (dimensions + GCPs), skipping pixel data."""
    with open(file_path, "rb") as f:
        w, h, c = struct.unpack("<ddd", f.read(24))
        w, h, c = int(w), int(h), int(c)

        gcps = []
        for _ in range(4):
            gcps.append(struct.unpack("<dddd", f.read(32)))

    tl, tr, bl, br = gcps
    lon_left = (tl[3] + bl[3]) / 2
    lon_right = (tr[3] + br[3]) / 2
    lat_top = (tl[2] + tr[2]) / 2
    lat_bottom = (bl[2] + br[2]) / 2

    pixel_scale_x = (lon_right - lon_left) / w
    pixel_scale_y = (lat_bottom - lat_top) / h
    affine = Affine(pixel_scale_x, 0, lon_left,
                    0, pixel_scale_y, lat_top)

    return BoundsResult(
        width=w,
        height=h,
        src_width=w,
        src_height=h,
        crs=CRS.from_epsg(4326),
        transform=affine,
    )


def read_gim(file_path: str, decimation_factor: int = 1) -> ReaderResult:
    """Read a GIM binary file."""
    with open(file_path, "rb") as f:
        # Header
        w, h, c = struct.unpack("<ddd", f.read(24))
        w, h, c = int(w), int(h), int(c)

        # GCPs: four corners [px_x, px_y, lat, lon]
        gcps = []
        for _ in range(4):
            gcps.append(struct.unpack("<dddd", f.read(32)))

        # Image data
        n_floats = w * h * c
        raw = np.frombuffer(f.read(n_floats * 4), dtype=np.float32)

    if c == 1:
        img = raw.reshape((h, w))
    else:
        img = raw.reshape((h, w, c))

    # Apply decimation
    dec = max(1, decimation_factor)
    if dec > 1:
        img = img[::dec, ::dec] if img.ndim == 2 else img[::dec, ::dec, :]
    dh, dw = img.shape[:2]

    # Convert float [0,1] → uint8 RGBA
    rgba = np.zeros((dh, dw, 4), dtype=np.uint8)
    if img.ndim == 2:
        grey = np.clip(img * 255, 0, 255).astype(np.uint8)
        rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = grey
    else:
        for i in range(min(c, 3)):
            rgba[:, :, i] = np.clip(img[:, :, i] * 255, 0, 255).astype(np.uint8)
        if c < 3:
            for i in range(c, 3):
                rgba[:, :, i] = rgba[:, :, 0]
    rgba[:, :, 3] = 255

    # Build an affine transform from GCPs (corners in lat/lon → WGS84)
    # GCPs: TL, TR, BL, BR  — each (px_x, px_y, lat, lon)
    tl, tr, bl, br = gcps
    # Map pixel → lon/lat using a simple affine fit from corners
    # X = lon, Y = lat
    lon_left = (tl[3] + bl[3]) / 2
    lon_right = (tr[3] + br[3]) / 2
    lat_top = (tl[2] + tr[2]) / 2
    lat_bottom = (bl[2] + br[2]) / 2

    pixel_scale_x = (lon_right - lon_left) / w
    pixel_scale_y = (lat_bottom - lat_top) / h  # negative (north-up)
    affine = Affine(pixel_scale_x, 0, lon_left,
                    0, pixel_scale_y, lat_top)

    return ReaderResult(
        rgba=rgba,
        width=dw,
        height=dh,
        src_width=w,
        src_height=h,
        crs=CRS.from_epsg(4326),
        transform=affine,
    )


registry.register(".gim", "gim_reader", read_gim, bounds_callback=read_gim_bounds)
