"""Custom reader support for non-GeoTIFF image formats.

Users can provide a Python script with a `reader(filename)` function that returns
both the image data and ground control points (GCPs) mapping pixel coordinates to lat/lon.

The reader function should return a tuple of (image_data, gcps):
    - image_data: numpy array of shape (H, W), (H, W, 3), or (H, W, 4) for
                  grayscale, RGB, or RGBA images respectively
    - gcps: list of at least 3 GCPs, each as [x, y, lat, lon] where:
        - x, y are pixel coordinates
        - lat, lon are WGS84 coordinates in decimal degrees

At least 3 GCPs are required; 4+ is recommended for best accuracy.
"""
import importlib.util
import math
from pathlib import Path
from typing import Callable

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform as transform_coords

# WGS84 CRS
WGS84 = CRS.from_epsg(4326)
# Web Mercator CRS
WEB_MERCATOR = CRS.from_epsg(3857)


def load_reader_function(script_path: str) -> Callable[[str], list]:
    """Load a reader function from a user-provided Python script.
    
    Args:
        script_path: Path to a Python file containing a `reader(filename)` function.
        
    Returns:
        The reader function.
        
    Raises:
        ValueError: If the script doesn't contain a valid reader function.
    """
    path = Path(script_path)
    if not path.exists():
        raise ValueError(f"Reader script not found: {script_path}")
    
    # Load the module dynamically
    spec = importlib.util.spec_from_file_location("custom_reader_module", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load reader script: {script_path}")
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    # Check for reader function
    if not hasattr(module, 'reader'):
        raise ValueError(f"Reader script must define a 'reader(filename)' function")
    
    reader_func = getattr(module, 'reader')
    if not callable(reader_func):
        raise ValueError(f"'reader' in script must be a callable function")
    
    return reader_func


def load_gcps_function(script_path: str) -> Callable[[str], tuple] | None:
    """Load a get_gcps function from a user-provided Python script.
    
    Args:
        script_path: Path to a Python file that may contain a `get_gcps(filename)` function.
        
    Returns:
        The get_gcps function, or None if not present in the script.
    """
    path = Path(script_path)
    if not path.exists():
        return None
    
    # Load the module dynamically
    spec = importlib.util.spec_from_file_location("custom_reader_module", path)
    if spec is None or spec.loader is None:
        return None
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    # Check for get_gcps function
    if hasattr(module, 'get_gcps'):
        gcps_func = getattr(module, 'get_gcps')
        if callable(gcps_func):
            return gcps_func
    
    return None


def gcps_to_affine(gcps: list, img_width: int, img_height: int) -> tuple[Affine, CRS]:
    """Compute an affine transform from GCPs using least-squares fitting.
    
    The affine transform maps pixel coordinates to Web Mercator coordinates.
    
    Args:
        gcps: List of [x, y, lat, lon] ground control points
        img_width: Image width in pixels
        img_height: Image height in pixels
        
    Returns:
        Tuple of (affine_transform, crs) where crs is Web Mercator
    """
    if len(gcps) < 3:
        raise ValueError("At least 3 GCPs are required to compute an affine transform")
    
    # Convert lat/lon to Web Mercator
    lats = [gcp[2] for gcp in gcps]
    lons = [gcp[3] for gcp in gcps]
    
    xs_mercator, ys_mercator = transform_coords(WGS84, WEB_MERCATOR, lons, lats)
    
    # Build matrices for least-squares affine fitting
    # We want to solve: [X_mercator, Y_mercator] = A * [x_pixel, y_pixel, 1]
    # Where A is a 2x3 affine matrix
    
    n = len(gcps)
    # Source pixel coordinates (augmented with 1 for translation)
    src = np.zeros((n, 3))
    for i, gcp in enumerate(gcps):
        src[i, 0] = gcp[0]  # x pixel
        src[i, 1] = gcp[1]  # y pixel
        src[i, 2] = 1.0     # for translation
    
    # Destination mercator coordinates
    dst_x = np.array(xs_mercator)
    dst_y = np.array(ys_mercator)
    
    # Solve using least squares: A = pinv(src) @ dst
    # For each component separately
    src_pinv = np.linalg.pinv(src)
    
    affine_x = src_pinv @ dst_x  # [a, b, c] for X = a*px + b*py + c
    affine_y = src_pinv @ dst_y  # [d, e, f] for Y = d*px + e*py + f
    
    # Create Affine transform
    # Affine(a, b, c, d, e, f) represents:
    # X = a*col + b*row + c
    # Y = d*col + e*row + f
    affine = Affine(
        affine_x[0],  # a: scale x
        affine_x[1],  # b: shear x
        affine_x[2],  # c: translate x
        affine_y[0],  # d: shear y
        affine_y[1],  # e: scale y (usually negative for image coords)
        affine_y[2],  # f: translate y
    )
    
    return affine, WEB_MERCATOR


def load_image_with_reader(
    file_path: str,
    reader_func: Callable[[str], tuple]
) -> tuple[np.ndarray, Affine, CRS, int, int]:
    """Load an image using a custom reader function.
    
    Args:
        file_path: Path to the image file
        reader_func: Function that takes filename and returns (image_data, gcps)
        
    Returns:
        Tuple of (rgba_array, affine_transform, crs, width, height)
    """
    # Get image data and GCPs from reader
    result = reader_func(file_path)
    
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise ValueError("Reader must return a tuple of (image_data, gcps)")
    
    image_data, gcps = result
    
    # Validate image data
    if not isinstance(image_data, np.ndarray):
        raise ValueError(f"Reader must return numpy array for image_data, got {type(image_data)}")
    
    if image_data.ndim not in (2, 3):
        raise ValueError(f"Image data must be 2D (grayscale) or 3D (RGB/RGBA), got {image_data.ndim}D")
    
    # Validate GCPs
    if not gcps or len(gcps) < 3:
        raise ValueError(f"Reader returned insufficient GCPs ({len(gcps) if gcps else 0}). Need at least 3.")
    
    for i, gcp in enumerate(gcps):
        if len(gcp) != 4:
            raise ValueError(f"GCP {i} has {len(gcp)} elements, expected 4: [x, y, lat, lon]")
    
    # Get image dimensions
    img_height, img_width = image_data.shape[:2]
    
    # Convert to RGBA
    if image_data.ndim == 2:
        # Grayscale -> RGBA
        rgba_array = np.zeros((img_height, img_width, 4), dtype=np.uint8)
        gray = image_data.astype(np.uint8) if image_data.dtype != np.uint8 else image_data
        rgba_array[:, :, 0] = gray
        rgba_array[:, :, 1] = gray
        rgba_array[:, :, 2] = gray
        rgba_array[:, :, 3] = 255
    elif image_data.shape[2] == 3:
        # RGB -> RGBA
        rgba_array = np.zeros((img_height, img_width, 4), dtype=np.uint8)
        rgb = image_data.astype(np.uint8) if image_data.dtype != np.uint8 else image_data
        rgba_array[:, :, :3] = rgb
        rgba_array[:, :, 3] = 255
    elif image_data.shape[2] == 4:
        # Already RGBA
        rgba_array = image_data.astype(np.uint8) if image_data.dtype != np.uint8 else image_data
    else:
        raise ValueError(f"Image must have 1, 3, or 4 channels, got {image_data.shape[2]}")
    
    # Compute affine transform from GCPs
    affine, crs = gcps_to_affine(gcps, img_width, img_height)
    
    return rgba_array, affine, crs, img_width, img_height


def compute_bounds_from_affine(affine: Affine, width: int, height: int) -> tuple[float, float, float, float]:
    """Compute bounding box from affine transform and image dimensions.
    
    Args:
        affine: Affine transform (pixel to world)
        width: Image width in pixels
        height: Image height in pixels
        
    Returns:
        Tuple of (west, south, east, north) in the affine's coordinate system
    """
    # Get corners in world coordinates
    corners = [
        affine * (0, 0),           # top-left
        affine * (width, 0),       # top-right
        affine * (width, height),  # bottom-right
        affine * (0, height),      # bottom-left
    ]
    
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    
    return (min(xs), min(ys), max(xs), max(ys))
